"""Booking use cases and the reconciliation loop (CONTRACT_NOTES 2.7 to 2.9).

A booking never takes or bumps the trip version: the expected revision and price protect the terms, and the poller must
not make other editors' edits stale. The attempt is committed as `unknown` before the POST, and no connection is held while
the provider answers: the work before and after it runs in the thread pool, as in app/services/itinerary.py.
"""
import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.context import request_id
from app.db import attempts
from app.db.models import BookingAttempt
from app.db.session import engine
from app.domain.booking import CLOSED_WITHOUT_BOOKING, MAY_REPLACE, Outcome, adopt, attempt_view, from_post, same_terms
from app.domain.errors import ApiError, not_found
from app.providers import client
from app.services import itinerary

logger = logging.getLogger("uvicorn.error")
BATCH = 20
# The unique constraints that two racers for one item collide on. The reference carries the attempt number, so it collides too.
RACE_LOST = ("uq_one_open_attempt", "uq_booking_attempt_number", "booking_attempts_client_reference_key")
MAX_RETRY_AFTER = 86400  # any digit count is a valid Retry-After (A40), but a day is the longest the poller honors

# Claims due attempts and moves their next check out in one statement, so two processes never take the same one and a
# crash during the provider call only delays the check. The interval after check number c (0-based) is intervals[c + 1],
# and the last one repeats: Postgres arrays are 1-based, so that is element LEAST(c + 2, n).
CLAIM = text(
    """
    UPDATE booking_attempts
    SET next_check_at = now() + make_interval(secs => (CAST(:intervals AS double precision[]))[LEAST(check_count + 2, :n)]),
        check_count = check_count + 1
    WHERE id IN (SELECT id FROM booking_attempts
                 WHERE state IN ('unknown', 'pending') AND next_check_at <= now()
                 ORDER BY next_check_at LIMIT :batch FOR UPDATE SKIP LOCKED)
    RETURNING id, client_reference
    """
)


def _blocked(attempt):
    """409 for the attempt that stops a new one: open, confirmed (A12), or closed in the very moment we lost the race."""
    if attempt.state == "confirmed":
        code, message = "ALREADY_BOOKED", "This item is already booked."
    elif attempt.state in CLOSED_WITHOUT_BOOKING:
        code, message = "BOOKING_CONFLICT", "Another booking for this item was made at the same moment. Review details.current."
    else:
        code, message = "BOOKING_IN_PROGRESS", "A booking for this item is already in progress. Review details.current."
    return ApiError(409, code, message, {"current": attempt_view(attempt)})


def _item_view(session, access, item_id):
    """The item as the itinerary view shows it (A25, A27). Ends the transaction."""
    view = itinerary.itinerary_view(session, access.user, access.trip_id)
    shown = next((item for item in view["items"] if item["id"] == str(item_id)), None)
    if shown is None:  # deleted since the first read
        raise not_found()
    return shown


def _open(session, access, text_id, revision, price):
    """Steps 2 to 5 of POST .../booking: the attempt, committed as `unknown`, or the refusal. Returns what the POST needs."""
    item_id, _position, offer = itinerary.find_item(session, access, None, text_id)
    latest = attempts.latest(session, item_id=item_id)
    if latest is not None and latest.state not in CLOSED_WITHOUT_BOOKING:
        raise _blocked(latest)
    if revision != offer["revision"] or price != offer["price"]["amount"]:
        raise ApiError(
            409,
            "TERMS_CHANGED",
            "The terms you confirmed are not the ones stored for this item. Review details.current and confirm again.",
            {"current": _item_view(session, access, item_id)},
        )
    if not offer["available"]:  # the application's expired label does not block: the provider decides (A28)
        raise ApiError(
            409,
            "OFFER_UNAVAILABLE",
            "The provider says this offer is not available.",
            {"current": _item_view(session, access, item_id)},
        )
    number = latest.attempt_no + 1 if latest else 1
    request = {
        "clientReference": f"trip-{access.trip_id}-item-{item_id}-attempt-{number}",  # A10, under 128 characters
        "offerId": offer["id"],
        "expectedOfferRevision": offer["revision"],
        "expectedPrice": offer["price"],  # exactly as stored: the terms the user reviewed
    }
    attempt_id, key = uuid4(), uuid4().hex
    try:
        session.execute(
            insert(BookingAttempt).values(
                id=attempt_id,
                trip_id=access.trip_id,
                item_id=item_id,
                attempt_no=number,
                idempotency_key=key,
                client_reference=request["clientReference"],
                request=request,
                state="unknown",
                requested_by=access.user,
                next_check_at=func.now() + timedelta(seconds=config.BOOKING_POLL_INTERVALS[0]),
            )
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if isinstance(error.orig, ForeignKeyViolation):  # the item was deleted under us
            raise not_found() from None
        if not (isinstance(error.orig, UniqueViolation) and error.orig.diag.constraint_name in RACE_LOST):
            raise  # a collision of a random id or key: effectively never, and not a race to explain
        # Another editor won. Never go on to a second POST, even when the winner has already closed: the user answers
        # with the winner's attempt in front of them. It is read again, because the rollback expired every entity.
        winner = attempts.latest(session, item_id=item_id)
        raise (_blocked(winner) if winner is not None else not_found()) from None
    return attempt_id, key, request



def _settle(session, attempt_id, outcome, checked=False, retry_after=None):
    """Applies an outcome and returns the attempt's view. The WHERE is the monotonic rule: a late answer never
    overwrites a state that already moved forward (2.7 rule 7). `outcome` None is a read that learned nothing."""
    values = {"last_checked_at": func.now()} if checked else {}
    if retry_after is not None:  # A40: never sooner than the provider asked, nor than the configured interval
        wait = timedelta(seconds=min(retry_after, MAX_RETRY_AFTER))
        values["next_check_at"] = func.greatest(BookingAttempt.next_check_at, func.now() + wait)
    if outcome is not None:
        values["state"] = outcome.state
        if outcome.booking is not None:
            values |= {"booking": outcome.booking, "last_error": None}
        else:
            values["last_error"] = outcome.error
    if values:
        may_replace = MAY_REPLACE[outcome.state if outcome else None]
        session.execute(update(BookingAttempt).where(BookingAttempt.id == attempt_id, BookingAttempt.state.in_(may_replace)).values(**values))
    attempt = session.scalar(select(BookingAttempt).where(BookingAttempt.id == attempt_id).execution_options(populate_existing=True))
    view = attempt_view(attempt) if attempt is not None else None
    session.commit()
    if view is None:  # deleted while its POST was in flight: only possible once the poller closed it as failed
        raise not_found()
    return view


async def _lookup(code, message, request):
    """409 IDEMPOTENCY_CONFLICT or REFERENCE_CONFLICT: a booking may exist, so read it (3.2). Anything but a usable
    answer leaves the attempt `unknown`, and an operator hears about it."""
    try:
        reply = await client.get_booking_by_reference(request["clientReference"])
    except client.ProviderError:
        reply = None
    if reply is not None and reply.status == 200 and (code == "IDEMPOTENCY_CONFLICT" or same_terms(reply.body, request)):
        return adopt(reply.body)
    logger.error("booking conflict %s on %s could not be reconciled", code, request["clientReference"])
    return Outcome("unknown", None, {"code": code, "message": message})


async def _post(first, key, request):
    try:
        reply = await client.create_booking(key, request)
    except client.ProviderError as error:  # timeout, connection, undeclared, non-JSON, schema-invalid: unknown (A42, A45)
        return Outcome("unknown", None, {"code": error.code, "message": error.message})
    if reply.status == 400:
        logger.error("the provider refused our booking request as malformed, this is our bug: %s", request["clientReference"])
    outcome = from_post(first, reply.status, reply.body, reply.retry_after)
    return outcome or await _lookup(reply.body["code"], reply.body["message"], request)


async def book(session, access, text_id, revision, price):
    attempt_id, key, request = await run_in_threadpool(_open, session, access, text_id, revision, price)
    logger.info("booking %s requestId=%s", request["clientReference"], request_id.get())
    outcome = await _post(True, key, request)
    return await run_in_threadpool(_settle, session, attempt_id, outcome)


def _retryable(session, access, text_id):
    item_id, _position, _offer = itinerary.find_item(session, access, None, text_id)
    latest = attempts.latest(session, item_id=item_id)
    if latest is None or latest.state != "unknown":
        # Nothing to resend. A failed or rejected attempt is never retried with its key: the user starts a new one (3.4).
        current = attempt_view(latest) if latest is not None else _item_view(session, access, item_id)
        raise ApiError(
            409, "NOTHING_TO_RETRY", "Only a booking whose outcome is unknown can be retried. Review details.current.", {"current": current}
        )
    found = latest.id, latest.idempotency_key, latest.request
    session.rollback()
    return found


async def retry(session, access, text_id):
    attempt_id, key, request = await run_in_threadpool(_retryable, session, access, text_id)
    logger.info("booking %s requestId=%s", request["clientReference"], request_id.get())
    outcome = await _post(False, key, request)  # the same key and the same request, byte for byte (A14)
    return await run_in_threadpool(_settle, session, attempt_id, outcome)


def read(session, access, text_id):
    item_id, _position, _offer = itinerary.find_item(session, access, None, text_id)
    latest = attempts.latest(session, item_id=item_id)
    if latest is None:
        raise not_found("Booking")
    return attempt_view(latest)


def _check_done(attempt_id, outcome, retry_after):
    try:
        with Session(engine) as session:
            _settle(session, attempt_id, outcome, checked=True, retry_after=retry_after)
    except ApiError:  # the item was deleted meanwhile: nothing left to update
        pass


async def _check(attempt_id, reference):
    """One read of one claimed attempt. Only a valid Booking changes its state: 404, 429, 503, a timeout or an invalid
    body change nothing (A13, A19, A42). It never POSTs (A14)."""
    request_id.set(f"poll-{uuid4().hex}")  # this coroutine runs as its own task, so the id is its own
    logger.info("reconciling %s requestId=%s", reference, request_id.get())
    outcome, retry_after = None, None
    try:
        reply = await client.get_booking_by_reference(reference)
        if reply.status == 200:
            outcome = adopt(reply.body)
        retry_after = reply.retry_after  # only a 429 with a valid header carries one
    except client.ProviderError:
        pass  # the call line says why
    await run_in_threadpool(_check_done, attempt_id, outcome, retry_after)


def _claim():
    with Session(engine) as session:
        claimed = session.execute(
            CLAIM, {"intervals": config.BOOKING_POLL_INTERVALS, "n": len(config.BOOKING_POLL_INTERVALS), "batch": BATCH}
        ).all()
        session.commit()
        return claimed


async def _guarded(attempt_id, reference):
    try:
        await _check(attempt_id, reference)
    except Exception:  # one attempt's failure never stops the others or the loop
        logger.exception("reconciliation of %s failed", reference)


async def reconcile_forever():
    """Started by the lifespan, one task per process. State lives in the database, so a restart resumes where it stopped.

    ponytail: every process polls on its own, waking every second at most, and booking_attempts has no index on
    next_check_at. Fine for few open attempts and few instances. Add a partial index on the open states, or move the loop
    to a worker, when either grows."""
    while True:
        try:
            claimed = await run_in_threadpool(_claim)
            await asyncio.gather(*(_guarded(row.id, row.client_reference) for row in claimed))
        except Exception:  # a crash of one poll never stops the loop
            logger.exception("reconciliation poll failed")
        await asyncio.sleep(min(1, config.BOOKING_POLL_INTERVALS[0]))
