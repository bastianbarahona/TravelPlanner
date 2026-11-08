# Iteration 4 brief: bookings and reconciliation

The single spec for both authors. Written by the Director on 2026-11-07 (UTC-3), from `docs/STATUS.md` 2.10 as the owner approved it. If this brief and `contracts/openAPI.json` disagree, the contract wins: stop and report.

Read first: `CLAUDE.md`, `ARCHITECTURE.md`, `docs/CONTRACT_NOTES.md` Sections 1.0, 1.7 to 1.12, 2 and 3, and A1 to A20, A25 to A27, A38, A40, A42, A45. A-numbers below point there.

## Who does what

| Author | Writes | Never touches |
|---|---|---|
| Claude Sonnet (`dev-principal`) | `app/`, migration `0003`, `docker-compose.yml` if needed, `README.md` skeleton, `ARCHITECTURE.md` sections for this iteration | `tests/`, `contracts/`, `docs/STATUS.md` |
| Codex | `tests/` (new suites, `app_support.py`, `conftest.py`) | `app/`, `contracts/`, docs. Codex writes the tests **from this brief, without reading `app/`** |

The Director runs every check. Codex's sandbox denies Python and Docker, so Codex reports what it could not run.

## Decisions (approved by the owner)

1. **Background loop.** One asyncio task per process, started by the FastAPI lifespan, stopped on shutdown. It claims due attempts in one statement, commits, then calls the provider with no transaction open, then applies the outcome:
   ```sql
   UPDATE booking_attempts SET next_check_at = now() + <interval>, check_count = check_count + 1
   WHERE id IN (SELECT id FROM booking_attempts
                WHERE state IN ('unknown','pending') AND next_check_at <= now()
                ORDER BY next_check_at LIMIT 20 FOR UPDATE SKIP LOCKED)
   RETURNING ...
   ```
   It only reads: `GET /bookings/by-reference/{clientReference}` (A14, A20). It never POSTs. It resumes from the database after a restart. No lease or fence for bookings: reads are idempotent and states only move forward (below). A crash of one poll never stops the loop: log it and continue.
2. **One item per offer (O6).** Unique index `uq_itinerary_offer` on `(trip_id, (offer->>'id'))`. Adding an offer already in the trip answers `409 OFFER_ALREADY_IN_ITINERARY` with `details.current` = the itinerary view, and spends no booking. The provider call may already have happened; that is fine.
3. **`booking_attempts`.** Committed as `unknown` **before** the POST (2.7 rule 1). Columns, at least: `id uuid pk`, `trip_id`, `item_id uuid NOT NULL REFERENCES itinerary_items(id)` (**no cascade**: RESTRICT), `attempt_no int`, `idempotency_key text UNIQUE`, `client_reference text UNIQUE`, `request jsonb` (the exact `CreateBookingRequest` sent), `state text CHECK IN ('unknown','pending','confirmed','failed','rejected')`, `booking jsonb NULL` (the provider's `Booking`, raw, A26), `last_error jsonb NULL`, `requested_by text`, `created_at`, `last_checked_at NULL`, `next_check_at`, `check_count int DEFAULT 0`. `UNIQUE (item_id, attempt_no)`. Partial unique index `uq_one_open_attempt` on `(item_id) WHERE state NOT IN ('rejected','failed')`: one open attempt per item, none after `confirmed` (A12).
   - Idempotency key: `uuid4().hex`. Reference (A10): `trip-{tripId}-item-{itemId}-attempt-{n}` (under 128 characters).
   - **Delete an item:** delete its `rejected` and `failed` attempts, then the item, in one transaction. An open or `confirmed` attempt makes it `409 ITEM_HAS_BOOKING` with `details.current` = itinerary view. The explicit check gives the message; the FK is the backstop for a concurrent booking (a `ForeignKeyViolation` must also become that 409, never a 500). Probed: 24 races of delete against book, one winner each, no orphan.
4. **Routes.** A booking never takes or bumps the trip `version`: the expected revision and price protect the terms, and the poller must not make other editors' edits stale.
5. **Diagnostics.** Middleware sets a request id `request-<32 hex>` in a `contextvars.ContextVar` for every request. Every error body's `requestId` is that id, and every response carries it in an `X-Request-Id` header. Each provider call logs exactly one line, at INFO or above, after it ends:
   `provider call op=<operationId> status=<http status or timeout|unreachable|invalid> requestId=<our id> providerRequestId=<Error.requestId or ->`
   The background loop sets its own id per claimed attempt, `poll-<32 hex>`. Logs go to stderr, as today. `README.md` gets a skeleton: what this is, run (`docker compose up`), test (the `.venv` commands), mutation check, and links to `ARCHITECTURE.md`, `docs/STATUS.md` and `provider-mock/README.md`.

## API

All money in our API is a `str` with exactly two decimals, as today. All paths are under `/trips/{tripId}/itinerary/items/{itemId}`. Writes join the existing `editors` router, reads the `readers` router, so the 401, 404, 403, then 422 order is inherited. A malformed, missing or other-trip `itemId` is the same 404 as today.

### `POST .../booking` (editors), body `{"expectedRevision": str, "expectedPriceUsd": str}`

Order:
1. Validation (422). `expectedRevision`: 1 or more characters, no NUL or lone surrogate. `expectedPriceUsd`: the money pattern.
2. Item exists (404).
3. Latest attempt: if open, `409 BOOKING_IN_PROGRESS`; if `confirmed`, `409 ALREADY_BOOKED`. Both with `details.current` = the attempt view. No provider call.
4. Snapshot check, no provider call on failure: `expectedRevision != offer.revision` or `expectedPriceUsd != offer.price.amount` → `409 TERMS_CHANGED` with `details.current` = the item as the itinerary view shows it (A25, A27). `offer.available` false → `409 OFFER_UNAVAILABLE`. The application's expired label does not block: the provider decides (A28).
5. Insert the attempt as `unknown`, `next_check_at = now() + first interval`, and commit. A unique violation on `uq_one_open_attempt` means another editor won: answer `409 BOOKING_IN_PROGRESS` (or `ALREADY_BOOKED`) with the winner's attempt. An FK violation means the item was deleted: 404.
6. `POST /bookings` with header `Idempotency-Key` and body `{clientReference, offerId: offer.id, expectedOfferRevision: offer.revision, expectedPrice: offer.price}` exactly as stored in `request`. One attempt, **never retried automatically** (A14), bounded by `PROVIDER_TIMEOUT_SECONDS`.
7. Apply the outcome (table below), then answer **201** with the attempt view, whatever the state. The state, not the HTTP code, says what happened.

### `POST .../booking/retry` (editors), body `{}` or none

Only when the latest attempt is `unknown`: resend the stored key and the stored `request`, byte-equal as JSON. Otherwise `409 NOTHING_TO_RETRY` with `details.current`. Answer **200** with the attempt view. A `failed` or `rejected` attempt is never retried with its key: the user starts a new attempt with `POST .../booking` (3.4).

### `GET .../booking` (readers)

The latest attempt view, or 404 `NOT_FOUND` when the item has none. Never calls the provider: it is the state endpoint the UI polls (A15).

### Attempt view

```json
{
  "id": "uuid", "itemId": "uuid", "attempt": 1,
  "state": "unknown|pending|confirmed|failed|rejected",
  "clientReference": "trip-...-attempt-1",
  "requested": {"offerId": "...", "expectedOfferRevision": "...", "expectedPriceUsd": "420.00"},
  "booking": {"...": "the provider's Booking, raw; omitted until known"},
  "lastError": {"code": "...", "message": "..."},
  "requestedBy": "ana",
  "createdAt": "...", "lastCheckedAt": "... (omitted until the first read)",
  "nextStep": "one sentence for the user"
}
```

Omitted, never `null`. `nextStep` by state: `unknown` says the outcome is not known yet, the application keeps checking, and the user may retry with the same terms; `pending` says the provider is processing; `confirmed` says booked; `failed` gives `booking.failure.message` (or a generic one when `failure` is missing, A16) and says a new booking needs reviewing current terms; `rejected` says no booking was made and why (from `lastError`): refresh to see new terms after `OFFER_CHANGED`, search again after `OFFER_UNAVAILABLE` or a 404.

### Itinerary view changes

Each item gains `"booking"`: its latest attempt view, omitted when none. `priceKind` is `confirmed` when the latest attempt is `confirmed`, and then the budget line uses `booking.offer.price.amount`, not the snapshot (US-05, A26). Update the rule text that says nothing is booked. Read attempts in the same `REPEATABLE READ` snapshot as the items.

## POST outcome to local state (`CONTRACT_NOTES.md` 2.8, the done-when of this iteration)

| Provider answer | First POST | Retry of `unknown` |
|---|---|---|
| `200`/`201`, valid `Booking` | adopt `status` | adopt `status` |
| `409 OFFER_CHANGED`, `409 OFFER_UNAVAILABLE` | `rejected` | `rejected` (A13) |
| `404` | `rejected` | `rejected` |
| `400` | `rejected`, logged as our bug (ERROR) | stays `unknown`, logged as our bug |
| `401` | stays `unknown`, ERROR log for the operator | stays `unknown` |
| `429` | stays `unknown`, `lastError` `RATE_LIMITED` with `retryAfterSeconds` when the header is valid | stays `unknown` |
| `503` | `unknown` (A1) | stays `unknown` |
| `409 IDEMPOTENCY_CONFLICT` | look up by reference: on `200` adopt the body; otherwise stay `unknown`, ERROR log | same |
| `409 REFERENCE_CONFLICT` | look up by reference: adopt only if `offer.id` and `offer.revision` equal the stored request (3.2); otherwise stay `unknown`, ERROR log | same |
| timeout, connection failure, undeclared status, non-JSON, schema-invalid body (A42, A45) | `unknown` | stays `unknown` |

`lastError` records `{code, message}` of the latest refusal or failure, our code names: the provider's `Error.code` when there is one, else `PROVIDER_UNREACHABLE` or `PROVIDER_INVALID_RESPONSE`. Clear it when a valid `Booking` is adopted.

**Monotonic transitions, enforced in the `UPDATE`'s `WHERE`:** `unknown` → any state; `pending` → `pending` (new `booking` body), `confirmed`, `failed`; `confirmed`, `failed`, `rejected` → never change (2.7 rule 7). A late POST answer `pending` after the poller already stored `confirmed` changes nothing.

Responses are parsed strictly against the contract (A45), as `app/providers/client.py` does for offers: `createBooking` declares 200, 201, 400, 401, 404, 409, 429, 503; `getBookingByReference` 200, 401, 404, 429, 503. A `Booking` also goes through the amount and instant checks, and its `clientReference` must equal ours, else it is invalid.

## Reconciliation (2.9)

The loop polls attempts in `unknown` or `pending`. By-reference `200` with a valid `Booking`: adopt it (monotonic). `404`, `429`, `503`, timeout, invalid: no state change (A13, A19, A42), only `last_checked_at`, `check_count` and the next interval. It never ends polling and never POSTs.

Configuration, environment only:

| Variable | Default | Meaning |
|---|---|---|
| `BOOKING_POLL_INTERVALS` | `5,10,30,60` | Seconds before the 1st, 2nd, 3rd check; the last repeats (A15) |
| `BOOKING_POLL_ENABLED` | `1` | `0` turns the loop off, for tests that must see the stored state untouched |

The loop wakes every `min(1, first interval)` seconds.

## Tests that must exist, and must be able to fail

Real HTTP, the mock and Postgres as real processes, as today. Use the mock for the real flows (its fixtures: `offer-hotel-002` pending then confirmed, `offer-hotel-003` pending then failed, `offer-hotel-004` failed, `offer-hotel-005` unavailable, any other confirmed; `MOCK_PENDING_DELAY_SECONDS` on a private mock), and `ProviderStub` for answers the mock cannot give. `empty_database` must also truncate `booking_attempts`.

1. **Every row of the 2.8 table above, both columns,** asserting the stored state, that the attempt keeps its key and reference, and the number of POSTs the provider received.
2. **Crash after commit,** two cases with a hard kill of the application (`AppProcess.kill`):
   a. The POST never reached the provider (the stub holds it): after restart the attempt is `unknown`; the poller's by-reference 404 keeps it `unknown`; a user retry sends the **same key and byte-equal body**, and the outcome is adopted.
   b. The provider accepted it but the answer was lost (a stub that forwards to the mock, then holds the answer): after restart, the poller reconciles by reference to `confirmed` with no second POST.
   Also: while the POST is held, the attempt row is already committed as `unknown` (read Postgres directly).
3. **Polling:** pending becomes confirmed and pending becomes failed without any user action; a by-reference 404 never turns `unknown` into anything; the loop never POSTs (count stub requests by method); polling resumes after an application restart; with `BOOKING_POLL_ENABLED=0` nothing changes.
4. **Terms (US-07):** a mismatched revision or price is `TERMS_CHANGED` with no provider call; an unavailable snapshot is refused with no call; a quote changed at the provider (mock control) gives `rejected` with `OFFER_CHANGED`, and the stored snapshot and price are untouched.
5. **One open attempt:** 24 concurrent `POST .../booking` on one item produce exactly one provider POST and one attempt, and every loser gets `BOOKING_IN_PROGRESS` or `ALREADY_BOOKED` with the winner. A new attempt after `rejected` and after `failed` is allowed, with a new key and reference; after `confirmed` it is refused.
6. **One item per offer:** adding the same offer twice is `409 OFFER_ALREADY_IN_ITINERARY`; concurrent adds of one offer create one item.
7. **Delete:** refused with an open or confirmed attempt; allowed after `rejected`/`failed`; a concurrent delete and book never leave both succeeding or an orphan attempt.
8. **Budget (US-05):** a confirmed item's line is `confirmed` at `booking.offer.price`, and stays so after the provider's price changes and the item is refreshed. Mixed outcomes on two items stay visible side by side (US-08).
9. **Permissions:** viewers get 403 on book and retry, nonparticipants 404, viewers can read `GET .../booking`. The `/openapi.json` walk covers the new routes with no edit.
10. **Diagnostics (O4):** `AppProcess` writes the application's stdout and stderr to a file a test can read. A provider error through the app produces one log line with the response's `requestId` (body or `X-Request-Id`) and the stub's `Error.requestId`. A success carries `X-Request-Id` matching `request-[0-9a-f]{32}`.
11. **Migrations:** `0003` applies on an empty database with no drift (`alembic check`), and direct SQL proves `uq_one_open_attempt`, the FK's RESTRICT, the state CHECK and `uq_itinerary_offer`.

Every new test must be able to fail for its stated reason. Timing: prefer waiting on a condition with a deadline over fixed sleeps.

## Out of scope

Documents, MCP, the frontend, cancellations, automatic rebooking, any change to the mock or the contract, any automatic POST.
