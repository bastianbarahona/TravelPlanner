# Iteration 5 brief: documents

**As built** (2026-11-08): implemented as written, with 13 tests and 30 killed mutations. The record, the departures (`nextStep` wording, HTML layout beyond the required texts) and the merge steps with Iteration 4 are in `docs/STATUS.md` 2.12. Codex was on a usage limit, so `qa-ux` wrote the tests.

The single spec for both authors. Written by the Director on 2026-11-07 (UTC-3), from `docs/STATUS.md` 2.10, with the five decisions the owner approved that day. If this brief and `CLAUDE.md` disagree, `CLAUDE.md` wins: stop and report.

Stories: US-09 (generate a document) and US-10 (access the right document). Read first: `CLAUDE.md`, `ARCHITECTURE.md` (Itinerary and its version, Estimates and confirmed prices), `docs/prd.md` US-09 and US-10.

## Who does what

| Author | Writes | Never touches |
|---|---|---|
| Claude Sonnet (`dev-principal`) | `app/` (new `services/documents.py`, `api/documents.py`, migration `0004`, models, `main.py` lifespan, `config.py`), the `ARCHITECTURE.md` sections for documents | `tests/`, `contracts/`, `docs/STATUS.md` |
| Codex | `tests/` (new `test_app_documents.py`, and `conftest.py` / `app_support.py` where needed) | `app/`, `contracts/`, docs. Codex writes the tests **from this brief, without reading `app/`** |

The Director runs every check. Codex's sandbox denies Python and Docker, so Codex reports what it could not run.

## Parallel with Iteration 4

Iteration 4 (bookings) is being built in another worktree and merges first. It adds `booking_attempts` (migration `0003`), an asyncio loop in the FastAPI lifespan, an item `booking` in the itinerary view, and `priceKind = confirmed`. This iteration must not depend on any of it:

- **Migration `0004`, `down_revision = "0002"`**, with the comment `# Iteration 4's 0003 merges first: set this to "0003" at the merge.` With `"0003"` today, `alembic upgrade head` fails.
- **Booking states come from a stub**, `booking_states(session, item_ids) -> dict[str, dict]`, in `app/services/documents.py`, called inside the source snapshot. Today it returns `{}`. Its docstring says the merge replaces it with the latest `booking_attempts` row per item (`{"state", "clientReference"}`), read in the same snapshot.
- **The worker is its own function,** `document_worker()`, started by a FastAPI `lifespan` in `app/main.py` and cancelled on shutdown. Iteration 4's loop goes beside it at the merge; neither calls the other.

## Decisions (approved by the owner)

1. **Format:** HTML built with the standard library. Every piece of user or provider text goes through `html.escape(..., quote=True)`. No new dependency.
2. **Storage and consistency:** the content is a `bytea` column in the document row, so the file and its reference are written in one transaction. The database refuses a `complete` row without content, and a row whose `content_sha256` is not the SHA-256 of `content`. Every download re-hashes the bytes; a mismatch marks the document `failed` and answers 409, and a retry regenerates it.
3. **Snapshot:** the source is captured **when the document is requested**, in one `REPEATABLE READ` snapshot (`begin_snapshot` in `app/db/session.py`): the trip, its items in position order, and each item's booking state. It is stored as JSONB `source`; the worker only renders it. Nothing that depends on the clock or on the caller is in it. A unique `(trip_id, source_sha256)` makes a repeated request return the same document; the `INSERT ... ON CONFLICT DO NOTHING` runs **after** the snapshot ends, in `READ COMMITTED` (probed: inside `REPEATABLE READ`, 11 of 12 concurrent inserts failed; outside it, 24 of 24 succeeded with one row).
4. **Jobs:** states `waiting`, `in_progress`, `complete`, `failed`. A claim takes a lease with a fresh token; the final write is fenced by that token. At most 3 attempts.
5. **API and migration:** below.

## Table `documents` (migration `0004`, and the model in `app/db/models.py`)

Exact names: tests read and write this table with SQL.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `trip_id` | `uuid NOT NULL` FK `trips(id)` | |
| `trip_version` | `integer NOT NULL` | the `trips.version` read in the snapshot |
| `source` | `jsonb NOT NULL` | below |
| `source_sha256` | `text NOT NULL` | hex SHA-256 of the canonical source JSON |
| `state` | `text NOT NULL` | |
| `requested_by` | `text NOT NULL` | the first requester's user id |
| `requested_at` | `timestamptz NOT NULL DEFAULT now()` | |
| `attempts` | `integer NOT NULL DEFAULT 0` | incremented by each claim |
| `lease_token` | `uuid NULL` | |
| `lease_until` | `timestamptz NULL` | |
| `content` | `bytea NULL` | |
| `content_sha256` | `text NULL` | hex |
| `completed_at` | `timestamptz NULL` | |
| `error` | `text NULL` | the user-facing reason of a `failed` document |

Constraints, with these names:

- `uq_documents_source UNIQUE (trip_id, source_sha256)`
- `ck_documents_state CHECK (state IN ('waiting','in_progress','complete','failed'))`
- `ck_documents_complete CHECK ((state = 'complete') = (content IS NOT NULL AND content_sha256 IS NOT NULL AND completed_at IS NOT NULL))`
- `ck_documents_hash CHECK (content IS NULL OR content_sha256 = encode(sha256(content), 'hex'))`
- `ck_documents_lease CHECK (state <> 'in_progress' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL))`

`alembic check` must report no drift.

## Source

```json
{
  "trip": {"id": "...", "destinations": ["..."], "startsOn": "...", "endsOn": "...", "budgetUsd": "...", "travelers": 2, "version": 3},
  "items": [{"id": "...", "position": 0, "offer": {"...": "the stored snapshot, raw"}, "priceKind": "estimate", "booking": {"state": "...", "clientReference": "..."}}],
  "budget": {"...": "app.domain.itinerary.budget(trip, items), as the itinerary view shows it"}
}
```

- `booking` is omitted when the stub has nothing for the item.
- Not in it: `role`, `participants`, `expired`, `bookable`, `issues`, `rules`, any `now()`. Two users requesting the same trip state get the same `source_sha256`.
- `source_sha256 = sha256(json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()`.
- `trip_version = source.trip.version`.

## Rendering

`render(document) -> bytes`, a pure function of the row's `id`, `source` and `requested_at`. The same row always renders the same bytes, so a regenerated document has its original `content_sha256`. UTF-8 HTML, `<!doctype html>`, `<meta charset="utf-8">`, no script, no external resource.

The HTML must contain these texts verbatim (values escaped):

- `Document {id}`
- `Trip version {trip.version}`
- `Captured at {requested_at}` with `requested_at` as ISO 8601 in UTC with a `Z`, e.g. `2026-11-08T03:12:45.123456Z`. The API's `requestedAt` uses the same string.
- every destination, `startsOn` and `endsOn`
- for every item: its offer `title`, `category`, `startsAt`, `endsAt`, its `price.amount` with ` USD`, its `priceKind`, and `Booking: {state}`, or `Booking: not requested` when there is none
- `Estimated {budget.estimatedUsd} USD`, `Confirmed {budget.confirmedUsd} USD`, `Total {budget.totalUsd} USD`, `Budget {budget.budgetUsd} USD`
- one sentence saying prices are the provider's quotes at capture time, and an estimate is not a confirmed price

## Worker

`document_worker()` runs while the app runs, when `DOCUMENT_WORKER_ENABLED` is not `0`. Database work runs in the thread pool.

1. **Claim** one document per statement:
   ```sql
   UPDATE documents SET state='in_progress', lease_token=:token, lease_until=now() + make_interval(secs => :lease), attempts=attempts+1
   WHERE id = (SELECT id FROM documents
               WHERE state='waiting' OR (state='in_progress' AND lease_until < now())
               ORDER BY requested_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)
   RETURNING ...
   ```
   Nothing claimed: sleep 0.5 s.
2. If `attempts > 3` after the claim: fenced write to `failed`, `error = "Generation was interrupted too many times. Retry to start again."`.
3. Otherwise, if `DOCUMENT_RENDER_DELAY_SECONDS > 0`, `await asyncio.sleep` that long (a test-only knob, so tests can see `in_progress`, kill mid-generation and steal a lease). Then render.
4. **Finish, fenced:** `UPDATE ... SET state='complete', content, content_sha256, completed_at=now(), lease_token=NULL, lease_until=NULL WHERE id=:id AND lease_token=:token AND state='in_progress'`. Zero rows means the lease was lost: log it and drop the result.
5. **A render exception:** fenced on the token too. With `attempts < 3`, back to `waiting` with the lease cleared; at 3, `failed` with `error = "The document could not be generated. Retry to try again."`. Log the exception.
6. Any exception in an iteration (a database outage included) is logged, then the loop sleeps 1 s and goes on. The loop never dies on its own.

Configuration, environment only, in `app/config.py`:

| Variable | Default | Meaning |
|---|---|---|
| `DOCUMENT_WORKER_ENABLED` | `1` | `0` leaves every document as it is stored |
| `DOCUMENT_LEASE_SECONDS` | `30` | how long a claim lasts before another worker may reclaim it |
| `DOCUMENT_RENDER_DELAY_SECONDS` | `0` | test-only, a sleep before rendering |

## API

Under `/trips/{tripId}/documents`. Writes join an `editors` router with `trip_editor`, reads a `readers` router with `trip_member`, so the 401, 404, 403 order is inherited and the `/openapi.json` walk covers the routes. A malformed, missing or other-trip `documentId` is the same `404 NOT_FOUND` as a missing trip (parse it like `_item` in `app/services/itinerary.py`).

### Document view

```json
{
  "id": "uuid", "tripId": "uuid", "tripVersion": 3,
  "state": "waiting|in_progress|complete|failed",
  "requestedBy": "ana", "requestedAt": "2026-11-08T03:12:45.123456Z",
  "attempts": 1,
  "completedAt": "... (complete only)",
  "sha256": "64 hex (complete only)",
  "sizeBytes": 1234,
  "error": "... (failed only)",
  "nextStep": "one sentence for the user"
}
```

Omitted, never `null`: `completedAt`, `sha256` and `sizeBytes` appear only when `complete`; `error` only when `failed`. `nextStep`: `waiting` says it is queued and the page can be reloaded; `in_progress` says it is being generated; `complete` says it can be downloaded; `failed` says to retry.

### Routes

| Route | Who | Answer |
|---|---|---|
| `POST /trips/{tripId}/documents` | editors | No body is read. Captures the source and inserts. **202** with the view when a document was created, **200** with the existing document's view, in whatever state, when the source matches one already stored. Never a 500 under concurrent identical requests. |
| `GET /trips/{tripId}/documents` | readers | **200** `{"documents": [view, ...]}`, newest `requestedAt` first, then `id`. Never calls the provider. |
| `GET /trips/{tripId}/documents/{documentId}` | readers | **200** with the view. This is the state endpoint a page polls. |
| `GET /trips/{tripId}/documents/{documentId}/content` | readers | `complete`: **200**, the bytes, `content-type: text/html; charset=utf-8`, `content-disposition: attachment; filename="trip-{tripId}-v{tripVersion}-{documentId}.html"`, `content-security-policy: default-src 'none'`, `x-content-type-options: nosniff`. Otherwise **409 `DOCUMENT_NOT_READY`**, `details.current` = the view. A hash mismatch: a fenced `UPDATE ... SET state='failed', content=NULL, content_sha256=NULL, completed_at=NULL, error='The stored file did not match its record. Retry to generate it again.' WHERE id=? AND state='complete' AND content_sha256=<the hash read>`, commit, then **409 `DOCUMENT_CORRUPT`** with `details.current` = the failed view. The bytes are never sent. |
| `POST /trips/{tripId}/documents/{documentId}/retry` | editors | `failed`: `state='waiting'`, `attempts=0`, `error=NULL`, **200** with the view. Otherwise **409 `NOTHING_TO_RETRY`**, `details.current` = the view. |

Error bodies keep the existing `Error` shape.

## Tests that must exist, and must be able to fail

All in `tests/test_app_documents.py`, over real HTTP with the app, the mock and Postgres as real processes, through `start_app(**env)` for the knobs. `empty_database` in `conftest.py` must also truncate `documents`. Wait on a condition with a deadline, never a bare fixed sleep. Read Postgres directly where the brief names a column.

1. **Lifecycle (US-09):** with `DOCUMENT_WORKER_ENABLED=0`, a POST is 202 `waiting`, stays `waiting` across GETs and a hard kill and restart, and its content is 409 `DOCUMENT_NOT_READY`. An app with the worker on then completes it. With `DOCUMENT_RENDER_DELAY_SECONDS`, `in_progress` is observable before `complete`. The downloaded bytes' SHA-256 equals `sha256` and `content_sha256` in Postgres, `sizeBytes` is their length, and the four headers are as specified.
2. **Content (US-09):** every required text appears for a trip with two items. A destination `<script>alert(1)</script>` appears escaped and never raw. Every item shows `Booking: not requested` today.
3. **Point in time (US-10):** generate at version N, then change the itinerary. The old document downloads byte-identical, still says `Trip version N`, and its view keeps `tripVersion` N. A new POST creates a different document at the new version. A viewer lists both.
4. **Repeated requests:** a second POST with no change answers 200 with the same id, also from another editor. 24 concurrent POSTs produce one row, every answer is 200 or 202 with the same id, and exactly one is 202.
5. **Interruption:** with a render delay longer than a short lease, a hard kill while `in_progress`, then an app without delay: the document becomes `complete`, `attempts` is 2, and the content downloads. Before it completes, nothing reports it `complete`.
6. **Fence:** while the worker sleeps in its render delay, the test overwrites `lease_token` and `lease_until` in SQL, as a reclaiming worker would. When the delay ends, the row is still `in_progress` with the test's token and no content. After the stolen lease expires, the worker reclaims it and completes it with `attempts` 2.
7. **Failure and retry:** a row inserted with an unrenderable `source` (`{}`) ends `failed` with `attempts` 3 and an `error`, never `complete`, and its content is 409 `DOCUMENT_NOT_READY`. Retry on it is 200 `waiting` with `attempts` 0. Retry on a `complete` or `waiting` document is 409 `NOTHING_TO_RETRY`.
8. **Stored file against its record (US-10):** the database refuses a `complete` row without content and an `UPDATE` that breaks the hash. To simulate storage corruption, the test drops `ck_documents_hash`, corrupts `content`, and restores the constraint in a `finally` after removing any bad row. The download is then 409 `DOCUMENT_CORRUPT`, Postgres shows `failed` with `content` NULL, and retry brings it back `complete` with its original `sha256` and byte-identical content.
9. **Permissions (US-02, US-10):** a viewer gets 403 on POST and retry, and can list, read and download. A nonparticipant gets 404 on all five routes. A malformed, missing or other-trip `documentId` gets the same 404 body as a missing trip, apart from `requestId`.
10. **Constraints:** direct SQL proves `ck_documents_state`, `ck_documents_complete`, `ck_documents_hash`, `ck_documents_lease` and `uq_documents_source`. Migrations still apply on an empty database with no drift.

## Out of scope

PDF, deleting documents, document expiry, notifications or push, the frontend, the MCP layer, any change to the mock or the contract, and any reading of `booking_attempts` before the merge.
