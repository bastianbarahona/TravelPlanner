"""Trip documents (US-09, US-10): the source is captured when a document is requested, a worker renders it later.

Consistency: the file lives in the document row, so the file and its reference commit together. The database refuses a
`complete` row without content, and content that does not match `content_sha256`. A download re-hashes the bytes.

Jobs: a claim takes a lease with a fresh token, and every write that ends a claim is fenced by that token, so a worker
whose lease was taken over cannot overwrite the new owner's result. No transaction stays open across an await.
"""
import asyncio
import hashlib
import html
import json
import logging
from datetime import UTC
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import config
from app.db.models import Document
from app.db.session import begin_snapshot, engine
from app.domain.errors import ApiError, not_found
from app.domain.itinerary import budget, price_kind
from app.services.itinerary import read_items
from app.services.trips import get_trip

logger = logging.getLogger("uvicorn.error")

MAX_ATTEMPTS = 3
INTERRUPTED = "Generation was interrupted too many times. Retry to start again."
RENDER_FAILED = "The document could not be generated. Retry to try again."
CORRUPT = "The stored file did not match its record. Retry to generate it again."
NEXT_STEP = {
    "waiting": "The document is queued. Reload this page in a moment to see its progress.",
    "in_progress": "The document is being generated. Reload this page in a moment.",
    "complete": "The document is ready. Download its content.",
    "failed": "Generation failed. Retry to generate it again.",
}

# What a view needs. The bytes are left out, and their length comes from Postgres.
_VIEW = (
    Document.id, Document.trip_id, Document.trip_version, Document.state, Document.requested_by,
    Document.requested_at, Document.attempts, Document.completed_at, Document.content_sha256, Document.error,
    func.octet_length(Document.content).label("size"),
)


def _instant(value):
    """ISO 8601 in UTC with a Z, always with microseconds: the API and the rendered file show the same string."""
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def view(row):
    result = {
        "id": str(row.id),
        "tripId": str(row.trip_id),
        "tripVersion": row.trip_version,
        "state": row.state,
        "requestedBy": row.requested_by,
        "requestedAt": _instant(row.requested_at),
        "attempts": row.attempts,
    }
    if row.state == "complete":
        result |= {"completedAt": _instant(row.completed_at), "sha256": row.content_sha256, "sizeBytes": row.size}
    if row.state == "failed":
        result["error"] = row.error
    result["nextStep"] = NEXT_STEP[row.state]
    return result


def capture_source(session, caller, trip_id):
    """The trip, its items and their booking states from one REPEATABLE READ snapshot. Nothing here depends on the
    clock or on the caller, so two users requesting the same trip state get the same source."""
    begin_snapshot(session)
    try:
        trip = get_trip(session, caller, trip_id)
        items = read_items(session, trip_id)  # each item carries its latest attempt's full view, built before the rollback
    finally:
        session.rollback()
    # priceKind and the budget need the full view: a confirmed item is charged the price in its booking (US-05).
    source_items = []
    for item in items:
        entry = {"id": item["id"], "position": item["position"], "offer": item["offer"], "priceKind": price_kind(item)}
        if "booking" in item:
            # Only these two. The view's lastCheckedAt, nextStep and checks change on every poll and would change the hash.
            entry["booking"] = {"state": item["booking"]["state"], "clientReference": item["booking"]["clientReference"]}
        source_items.append(entry)
    keys = ("id", "destinations", "startsOn", "endsOn", "budgetUsd", "travelers", "version")
    return {"trip": {key: trip[key] for key in keys}, "items": source_items, "budget": budget(trip, items)}


def _digest(source):
    canonical = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create(session, access):
    """(created, view). The insert runs after the snapshot has ended: inside REPEATABLE READ, concurrent identical
    inserts fail with serialization errors (11 of 12 in the probe), and here they resolve through the unique key."""
    source = capture_source(session, access.user, access.trip_id)
    digest = _digest(source)
    created = session.execute(
        pg_insert(Document)
        .values(
            id=uuid4(), trip_id=access.trip_id, trip_version=source["trip"]["version"], source=source,
            source_sha256=digest, state="waiting", requested_by=access.user,
        )
        .on_conflict_do_nothing(constraint="uq_documents_source")
        .returning(Document.id)
    ).scalar()
    session.commit()
    document_id = created or session.scalar(
        select(Document.id).where(Document.trip_id == access.trip_id, Document.source_sha256 == digest)
    )
    return created is not None, view(session.execute(select(*_VIEW).where(Document.id == document_id)).one())


def _row(session, access, raw_id, *columns):
    """A malformed, missing or other-trip id is the same 404 as a missing trip."""
    try:
        document_id = UUID(raw_id)
    except ValueError:
        raise not_found() from None
    row = None
    if str(document_id) == raw_id.lower():  # UUID() also takes braces, urn: and underscores
        row = session.execute(
            select(*(columns or _VIEW)).where(Document.id == document_id, Document.trip_id == access.trip_id)
        ).one_or_none()
    if row is None:
        raise not_found()
    return row


def list_documents(session, access):
    rows = session.execute(
        select(*_VIEW).where(Document.trip_id == access.trip_id).order_by(Document.requested_at.desc(), Document.id)
    ).all()
    return {"documents": [view(row) for row in rows]}


def get_document(session, access, raw_id):
    return view(_row(session, access, raw_id))


def _not_ready(current):
    return ApiError(409, "DOCUMENT_NOT_READY", "The document is not ready to download. See details.current.", {"current": current})


def read_content(session, access, raw_id):
    """(bytes, filename). Only a complete document whose bytes match their hash is ever sent."""
    row = _row(session, access, raw_id, *_VIEW, Document.content)
    if row.state != "complete":
        raise _not_ready(view(row))
    content = bytes(row.content)
    if hashlib.sha256(content).hexdigest() != row.content_sha256:
        session.execute(
            update(Document)
            .where(Document.id == row.id, Document.state == "complete", Document.content_sha256 == row.content_sha256)
            .values(state="failed", content=None, content_sha256=None, completed_at=None, error=CORRUPT)
        )
        session.commit()
        raise ApiError(
            409, "DOCUMENT_CORRUPT", CORRUPT,
            {"current": get_document(session, access, raw_id)},
        )
    return content, f"trip-{row.trip_id}-v{row.trip_version}-{row.id}.html"


def retry(session, access, raw_id):
    row = _row(session, access, raw_id)
    won = session.execute(
        update(Document)
        .where(Document.id == row.id, Document.state == "failed")
        .values(state="waiting", attempts=0, error=None, lease_token=None, lease_until=None)
    )
    session.commit()
    current = view(_row(session, access, raw_id))
    if won.rowcount == 0:
        raise ApiError(409, "NOTHING_TO_RETRY", "Only a failed document can be retried. See details.current.", {"current": current})
    return current


# Rendering. A pure function of the row's id, source and requested_at: the same row always gives the same bytes,
# so a regenerated document has its original content_sha256.

def _e(value):
    return html.escape(str(value), quote=True)


def render(document):
    source = document["source"]
    trip, items, totals = source["trip"], source["items"], source["budget"]
    amounts = {line["itemId"]: line["amountUsd"] for line in totals["lines"]}  # what the budget charges: the accepted price once confirmed
    parts = [
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">',
        f"<title>Trip document {_e(document['id'])}</title></head><body>",
        f"<h1>Document {_e(document['id'])}</h1>",
        f"<p>Trip version {_e(trip['version'])}</p>",
        f"<p>Captured at {_e(_instant(document['requested_at']))}</p>",
        "<h2>Trip</h2><ul>",
        *(f"<li>Destination: {_e(name)}</li>" for name in trip["destinations"]),
        f"</ul><p>Starts on {_e(trip['startsOn'])}</p><p>Ends on {_e(trip['endsOn'])}</p>",
        f"<p>Travelers {_e(trip['travelers'])}</p>",
        "<h2>Itinerary</h2><ol>",
    ]
    for item in items:
        offer, booking = item["offer"], item.get("booking")
        parts += [
            f"<li><h3>{_e(offer['title'])}</h3>",
            f"<p>Category: {_e(offer['category'])}</p>",
            f"<p>Starts at {_e(offer['startsAt'])}</p><p>Ends at {_e(offer['endsAt'])}</p>",
            f"<p>{_e(amounts[item['id']])} USD</p><p>Price kind: {_e(item['priceKind'])}</p>",
            f"<p>Booking: {_e(booking['state']) if booking else 'not requested'}</p>",
            f"<p>Reference: {_e(booking['clientReference'])}</p></li>" if booking else "</li>",
        ]
    parts += [
        "</ol><h2>Budget</h2>",
        f"<p>Estimated {_e(totals['estimatedUsd'])} USD</p><p>Confirmed {_e(totals['confirmedUsd'])} USD</p>",
        f"<p>Total {_e(totals['totalUsd'])} USD</p><p>Budget {_e(totals['budgetUsd'])} USD</p>",
        "<p>Prices are the provider's quotes at capture time, and an estimate is not a confirmed price.</p>",
        "</body></html>\n",
    ]
    return "".join(parts).encode("utf-8")


# The worker.

_CLAIM = text(
    """
    UPDATE documents SET state='in_progress', lease_token=:token,
        lease_until=now() + make_interval(secs => CAST(:lease AS double precision)), attempts=attempts+1
    WHERE id = (SELECT id FROM documents
                WHERE state='waiting' OR (state='in_progress' AND lease_until < now())
                ORDER BY requested_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)
    RETURNING id, source, requested_at, attempts
    """
)
_FENCE = "WHERE id=:id AND lease_token=:token AND state='in_progress'"
_FINISH = text(
    "UPDATE documents SET state='complete', content=:content, content_sha256=:sha, completed_at=now(), "
    f"lease_token=NULL, lease_until=NULL {_FENCE}"
)
_SETTLE = text(
    f"UPDATE documents SET state=:state, error=:error, lease_token=NULL, lease_until=NULL {_FENCE}"
)


def _run(statement, **params):
    with engine.begin() as connection:
        return connection.execute(statement, params)


def _claim(token):
    with engine.begin() as connection:
        return connection.execute(_CLAIM, {"token": token, "lease": config.DOCUMENT_LEASE_SECONDS}).mappings().first()


def _fenced(statement, claim, token, **params):
    """Zero rows means the lease was lost: another worker owns the document now, so this result is dropped."""
    if _run(statement, id=claim["id"], token=token, **params).rowcount == 0:
        logger.warning("document %s: lease lost, result dropped", claim["id"])


async def _work_one():
    token = uuid4()
    claim = await asyncio.to_thread(_claim, token)
    if claim is None:
        return False
    if claim["attempts"] > MAX_ATTEMPTS:
        await asyncio.to_thread(_fenced, _SETTLE, claim, token, state="failed", error=INTERRUPTED)
        return True
    if config.DOCUMENT_RENDER_DELAY_SECONDS > 0:  # test-only: lets a test see in_progress, kill the app or steal the lease
        await asyncio.sleep(config.DOCUMENT_RENDER_DELAY_SECONDS)
    try:
        content = render(claim)
    except Exception:
        logger.exception("document %s: render failed on attempt %s", claim["id"], claim["attempts"])
        state, error = ("waiting", None) if claim["attempts"] < MAX_ATTEMPTS else ("failed", RENDER_FAILED)
        await asyncio.to_thread(_fenced, _SETTLE, claim, token, state=state, error=error)
        return True
    sha = hashlib.sha256(content).hexdigest()
    await asyncio.to_thread(_fenced, _FINISH, claim, token, content=content, sha=sha)
    return True


async def document_worker():
    """Runs for as long as the app does. An iteration that raises, a database outage included, is logged and the
    loop goes on. ponytail: one claim at a time per process, and SKIP LOCKED lets several processes share the queue.
    Poll every 0.5 s when idle: LISTEN/NOTIFY if latency or idle load ever matter."""
    while True:
        try:
            if not await _work_one():
                await asyncio.sleep(0.5)
        except Exception:
            logger.exception("document worker iteration failed")
            await asyncio.sleep(1)
