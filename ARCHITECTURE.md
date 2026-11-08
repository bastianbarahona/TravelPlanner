# Architecture

Decisions, alternatives, guarantees, limits, risks and scaling. This file grows with each iteration. The provider mock, the application core, the provider client, search, the itinerary, the budget, bookings and their reconciliation exist. Anything here about a later iteration is a commitment, not built behavior.

The table of decisions in `CLAUDE.md` is the short version. This file holds the reasoning.

## Decisions

### Modular monolith

**Decision:** one application in Docker Compose, with one Postgres. Confirmed by the owner on 2026-11-07. Microservices were rejected.

**Why:** the PRD's strongest demands need immediate transactional consistency: concurrent edits (US-06), recovery from partial bookings (US-08) and immutable history. Microservices would need sagas and a transactional outbox, adding failure surface with no scale benefit at this stage. The candidate guide accepts either, and says more processes earn nothing.

**What this does not buy:** the provider sits outside every transaction, so a monolith does not make a booking atomic. US-08 rests on committing the attempt before the POST, and on reconciliation (`docs/CONTRACT_NOTES.md`, 2.7 and 2.9). Those are needed in either architecture.

**Growth path:** domains stay separated in code, so the document worker and the MCP server can run as separate processes from the same codebase, then move to their own containers if traffic justifies it. Whether they start that way is decided in Iterations 5 and 6.

### Optimistic locking

**Decision:** an integer `version` column, checked inside a Postgres transaction. A stale write gets a 409 with the current state.

**Why:** contention is low, it is simple to explain, and the 409 is usable. Pessimistic locks and CRDTs were rejected. This `version` is ours, and it is unrelated to the provider's `revision`, which is an opaque string.

**How it is built:** `PATCH /trips/{tripId}` compares `version` in Python first, so a stale request fails early. The real guard is `WHERE id = ? AND version = ?` on the `UPDATE`, which is what two writers holding the same `version` collide on. Destinations change only after that `UPDATE` wins, in the same transaction.

### Money

**Decision:** no binary floating point anywhere money flows. `Decimal` in the domain, `NUMERIC(12,2)` in Postgres, and a string with exactly two decimals in JSON. Rounding is `ROUND_HALF_UP`, in one place: `app/money.py`.

**Why:** US-05 needs reproducible totals.

**Boundary:** an API field is a `str`, converted by `app/money.py`. It is not a `Decimal` field. Tested with Pydantic: a `Decimal` field accepts the JSON number `420.1` even with `strict=True`, accepts `"420.10\n"`, and emits `"420"` or `"1E+3"`.

**The pattern is tighter than the contract's:** it stops at ten integer digits. The contract's pattern accepts `10000000000.00`, which overflows `NUMERIC(12,2)` and answered 500 when tested (A44).

### Migrations

**Decision:** Alembic with SQLAlchemy 2, forward-only, applied by a one-shot `migrate` service that the application waits for.

**Why one service:** two simultaneous `alembic upgrade head` runs on an empty database failed in 8 of 8 rounds, so application instances must never migrate themselves.

**What `alembic check` sees:** with `compare_server_default=True` it caught 8 of 11 drifts tested, and 7 without it. It misses changed and removed `CHECK` constraints and changed partial index predicates, so direct SQL tests cover those (`tests/test_app_database.py`).

### Test identities

**Decision:** `APP_TOKENS` holds `token:user` pairs, read at startup. There is no `users` table. The token alone decides who the caller is, and a client-supplied user id is never trusted.

**Why:** the candidate guide needs test identities only. A real identity provider replaces `current_user` and the two lookups of configured users, in `app/api/schemas.py` and `app/api/deps.py`.

### Refusals and roles

**Decision:** every protected route refuses in the same order, once its body has been decoded: 401 for a missing or bad token, 404 for a nonparticipant, a missing trip or a malformed id, 403 for a participant without the role, then 422. Trip fields and participants belong to the organizer, who is the creator. The itinerary belongs to editors and the organizer (`trip_editor`). Every participant reads the trip and its itinerary, and searches.

**Why:** the 404 hides whether a trip exists, as the provider does. Dependencies sit on the routers, so a new route cannot forget them. A walk over `/openapi.json` covers a new route with no test edit.

### Provider client

**Decision:** one `httpx.AsyncClient` in `app/providers/client.py`. Only the routes that call the provider are `async`, and no database transaction stays open while they wait: each service that calls the provider rolls back its earlier reads first, and database work after the call runs in the thread pool.

**Why, measured:** with the provider holding 60 searches for 3 seconds, the four combinations took this long, against 3.1 s for the same load sent straight to the mock.

| Route | Session during the call | All 60 searches | `/health`, slowest |
|---|---|---|---|
| sync | held | 15.4 s | 2.97 s |
| sync | released | 6.4 s | 2.89 s |
| async | held | 12.6 s | 3.06 s |
| async | released | 3.7 s | 0.12 s |

The compose healthcheck times out at 3 seconds, so every other combination risked flapping it.

**Timeouts (A43):** `PROVIDER_TIMEOUT_SECONDS`, 10 by default, with a 2 second connect. httpx's timeout bounds each read, not the response, so a body dripping one byte at a time ran 3.8 s past a 1 s timeout. `asyncio.timeout` around every call bounds the whole response. httpx's 5 second default equals the mock's supplier deadline, and with it a partial search arrived as a timeout.

**Retries:** a search is never retried, so a failed leg reaches the user at once (A35). `getOffer` makes at most two attempts, and only after a 429 or a 503: it waits `Retry-After`, or 5 s when that header is absent or invalid, and 2 to 3 s after a 503. A wait above 5 s is reported to the user, never slept and never shortened (A40). One deadline of `PROVIDER_TIMEOUT_SECONDS` covers both attempts and the wait (owner's O7), so no request waits on the provider longer than that.

**Parsing (A45):** responses are validated at runtime against `contracts/openAPI.json` itself, as the mock validates requests, so there is no second copy of the schemas, and each operation's declared statuses also come from the contract. Three checks sit on top, because the schema cannot express them:

- Amounts go through `app/money.py` (A44). The contract's `$` also lets `"420.00\n"` through.
- Instants go through `parse_instant`. Lowercase `t` and `z` are contract-valid, and `fromisoformat` refuses them.
- No string, key or value, may hold a NUL or a lone surrogate. jsonb refuses the first, and a response cannot encode the second.

Tested on 24 invalid offers: the schema with formats rejected 22, and the two it let through are the newline, which `parse_amount` closes, and `2.0` for an integer, which JSON Schema allows. A hand-written strict Pydantic model rejected 24, but it accepted a comma in a fraction of a second, which the contract refuses, and it would be a second copy of the schemas. The raw decoded body is what the application keeps (`docs/CONTRACT_NOTES.md`, 1.0 rule 4).

**Paths:** an offer id is percent-encoded with `quote(offer_id, safe="")`. Sent as it came, `offer-hotel-001/../offer-hotel-002` returned another offer.

### Search

**Decision:** `POST /trips/{tripId}/search` sends one provider request per destination (A37), concurrently, and answers one leg per destination. A leg is `complete`, `partial`, `empty` or `failed`, and a failure in one leg never loses the others. `travelers` always comes from the trip, and dates and destinations default to the trip's. Nothing is cached or stored: every search is live, and `searchedAt` is the provider's.

### Itinerary and its version

**Decision:** `itinerary_items` holds `position` and the provider's offer as JSONB, exactly as `GET /offers/{offerId}` returned it. The client sends only an `offerId`, so a price can never come from the client. Every itinerary write that changes state bumps `trips.version` through the same guarded `UPDATE` that `PATCH /trips` uses, and there is no second version.

**Why one version:** one number then names one state of the trip's planning data, its fields and its itinerary, which is what a document must cite in Iteration 5 (US-10). Participants are outside it, as "Limits I accept" says. The cost: an organizer's budget edit makes an editor's pending itinerary edit stale, and that edit gets a 409 with the current state.

**How it is built:**

- **Order of checks:** a write compares `version` in Python before calling the provider, so a stale request spends no provider call, and a huge integer never reaches the `UPDATE`.
- **A refusal that depends on item state:** a 404 for a missing item, or a 422 for a wrong permutation, rechecks the version first, so a concurrent commit still answers 409.
- **Positions:** `UNIQUE (trip_id, position)` is `DEFERRABLE INITIALLY DEFERRED`, because a plain unique constraint failed a two-item swap.
- **Reads:** every view the API returns is read in one `REPEATABLE READ` transaction: trips, the trip list, the itinerary, and the `details.current` of every 409. So `version` always matches the destinations and items it is shown with. A torn read was reproduced under `READ COMMITTED` by holding a lock on `trip_destinations`.
- **Refresh:** `POST .../items/{itemId}/refresh` replaces the snapshot only when the provider's body differs, and bumps `version` only then (A25).

**Issues, computed at read time and never stored:** `MISSING_ORIGIN` (A30), `DESTINATION_WITHOUT_STAY`, `PARTY_MISMATCH` (A22), `PRICE_EXPIRED` (A28, the application's clock, a hint), `UNAVAILABLE`, `OUTSIDE_TRIP_DATES` (the literal local date, A31) and `SCHEDULE_OVERLAP`.

**Schedule conflicts:** instants, half-open intervals, so touching is not an overlap. A pair conflicts when its categories are equal, or are transport and activity. An accommodation conflicts only with another accommodation, because travelers leave a hotel during a stay. On the fixtures' coherent Lisbon plan, a naive overlap reported two false conflicts, hotel against activities, and this rule reports none. `10:00+01:00` and `10:00Z` are different instants, although their clock reading is the same. The response explains every simplified rule in `rules`.

### Bookings and reconciliation

**Decision:** a booking is an attempt in `booking_attempts`, committed as `unknown` before the POST is sent. The provider sits outside every transaction, so a crash after that commit leaves an attempt we can resume, never a booking we cannot find (`docs/CONTRACT_NOTES.md`, 2.7).

**One attempt, one key, one reference.** The key is `uuid4().hex`. The reference is `trip-{tripId}-item-{itemId}-attempt-{n}` (A10). The attempt stores the exact `CreateBookingRequest`, and a retry sends the stored key and that request again, as the same bytes (sorted keys), whatever order jsonb returned its keys in. Only the user sends a POST: `POST .../booking` once, `POST .../booking/retry` for an `unknown` attempt, whose body is absent or `{}` (a JSON `null` is a 422). Nothing in the background ever POSTs (A14).

**One open attempt per item, in the database.** `uq_one_open_attempt` is a partial unique index on `item_id` where the state is not `rejected` or `failed`, so one attempt is open or confirmed at a time (A12). Of 24 concurrent bookings of one item, one wins and the rest answer `BOOKING_IN_PROGRESS` or `ALREADY_BOOKED` with the winner's attempt. A loser that hits `uq_one_open_attempt` or `UNIQUE (item_id, attempt_no)` never tries again: it reads the winner and answers 409 with it, and if the winner already closed as `failed` or `rejected` the code is `BOOKING_CONFLICT`. Looping on it let a fast winner close and a loser book a second time, which was found in review and is tested. `UNIQUE (item_id, attempt_no)` is the backstop for the number.

**A booking never takes the trip `version`.** The expected revision and price protect the terms, and a poller that bumped the version would make every other editor's edit stale.

**Outcomes (2.8).** `app/domain/booking.py` holds the table as a pure function. The state comes from `Booking.status`, never from the HTTP code. A timeout, a connection failure, an undeclared status, a non-JSON body and a schema-invalid body all leave `unknown` (A42, A45). A `Booking` whose `clientReference` is not ours is invalid. The two 409 conflicts look the booking up by reference: `IDEMPOTENCY_CONFLICT` adopts it, `REFERENCE_CONFLICT` adopts it only when `offer.id` and `offer.revision` equal the stored request.

**Monotonic transitions, in the `UPDATE`'s `WHERE`.** `unknown` moves anywhere, `pending` moves to `pending`, `confirmed` or `failed`, and a closed state never changes (2.7, rule 7). So a late POST answer of `pending` after the poller stored `confirmed` changes nothing, and no lock or fence is needed: reads are idempotent and states only move forward.

**Reconciliation loop.** One asyncio task per process, started by the lifespan. It claims due attempts in one `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING`, which also moves `next_check_at` out, commits, and only then calls the provider with no transaction open. It reads `GET /bookings/by-reference/{clientReference}` (A20) and adopts a valid `Booking`. A 404, 429, 503, timeout or invalid body changes no state, only `last_checked_at`, `check_count` and the next interval. A 429 with a valid `Retry-After` moves `next_check_at` to the later of the configured interval and now plus that wait (capped at a day), so the poller never asks sooner than the provider said (A40). It never ends polling. It resumes from the database after a restart, and an exception in one poll is logged and the loop goes on. `BOOKING_POLL_INTERVALS` (default `5,10,30,60`, the last repeats) and `BOOKING_POLL_ENABLED` are the only settings.

**What the user sees.** The attempt view says what happened and what to do next in `nextStep`. A `POST .../booking` answers 201 whatever the state, because the state, not the HTTP code, is the outcome. `GET .../booking` never calls the provider: it is the state endpoint the UI polls (A15). An item shows its latest attempt as `booking`.

### One item per offer

**Decision:** `uq_itinerary_offer`, a unique index on `(trip_id, offer->>'id')`. Adding an offer already in the trip answers `409 OFFER_ALREADY_IN_ITINERARY` with the itinerary as `details.current`, and the bump of the version rolls back with it. The provider call may already have happened, which costs nothing.

**Why:** a booking belongs to an item, and the provider deduplicates per intent, not per offer (A12). Two items for one offer could book it twice.

**Deleting an item.** Its `rejected` and `failed` attempts are deleted with it, in one transaction. An open or `confirmed` attempt answers `409 ITEM_HAS_BOOKING`. The foreign key from `booking_attempts.item_id` is `RESTRICT`, never `CASCADE`, and it is the backstop for a booking that commits between the check and the delete: that `ForeignKeyViolation` becomes the same 409. The Director probed this by hand with 24 races of delete against book: one winner each time, and no orphan attempt. The automated regression runs 20 rounds.

### Diagnostics

**Decision:** a pure ASGI middleware sets a request id, `request-<32 hex>`, in a `ContextVar`. Every error body's `requestId` is that id, and every response, a 500 included, carries it in `X-Request-Id`. Each provider call logs exactly one line, at INFO or above, after it ends: `provider call op=<operationId> status=<http status or timeout|unreachable|invalid> requestId=<ours> providerRequestId=<Error.requestId or ->`. The reconciliation loop sets `poll-<32 hex>` for each attempt it checks. Logs go to stderr.

**Why:** an operator who holds one of our `requestId`s must reach the provider's `requestId` for the same call (owner's O4), and a provider error is otherwise invisible to the person who reads our logs.

### Party size

**Decision:** `trips.travelers`, from 1 to 20, required on create and changed by the organizer. The provider requires a party on every search, and searching Lisbon for 3 travelers answered an empty result, which reads like "no options". The migration gives existing trips 1 traveler.

### Estimates and confirmed prices

**Decision:** `priceKind` is derived, not stored. It is `confirmed` when the item's latest attempt is `confirmed`, and then its budget line uses `booking.offer.price.amount`, the price the provider accepted, not the snapshot. Later price changes and refreshes never touch it (US-05, A26). Every other item is an `estimate`. The budget reports `estimatedUsd`, `confirmedUsd` and their total, so the distinction needs no new meaning for any column. The itinerary view reads the attempts in the same `REPEATABLE READ` snapshot as the items.

**Rounding:** totals are exact `Decimal` sums of two-decimal prices, so nothing rounds today. `format_amount` quantizes half up all the same. Before this iteration it formatted with `:.2f`, which rounds half even and turned `0.125` into `0.12`. A source scan fails on any other rounding primitive in `app/`.

## Proposals recorded, not adopted

From the owner's notes. They belong to later iterations and stay open until then.

| Proposal | Decide in | What the review found |
|---|---|---|
| The assistant reads trips only through read-only SQL views, such as `v_mcp_trip_itinerary`, with the user filter added in Python | Iteration 6 | Postgres views take no parameters, so the filter lives in Python either way, and forgetting it leaks data. The view adds no permission check. What blocks mutations is a read-only database role, and the MCP layer exposing no write tools. "Only through views" also cannot cover option search, which US-12 requires and which comes from the provider. The likely simpler shape is MCP tools calling the same `services/` and the same permission check as the API. |
| Separate `local_status` and `external_status` on a booking | Iteration 4 | **Resolved in Iteration 4, as one state plus the provider's raw record.** `state` is ours (`unknown`, `rejected` and the provider's three), and `booking` holds the provider's `Booking` as received, with its own `status`, so the provider's word is never lost and no second status column can drift from it. The separation matches US-08. Two details needed correcting. The reference cannot be one immutable UUID: each attempt has its own key and reference (A10, and 2.7 rules 4 and 5). Passive reconciliation alone cannot close `unknown` negatively, because a by-reference 404 is not proof, and only a same-key retry by the user can do that (A13, A14). |

## Questions closed by the contract

| Question | Answer |
|---|---|
| Does an offer carry an exact expiry, or must the backend infer a cache lifetime? | It carries one. `validUntil` is a required `Offer` field (`:949`): "Quote expires at this instant; a new acceptance at or after it must be rejected." No TTL is inferred, and `POST /bookings` is the only authority (A28). |
| Must the API take a distributed lock, such as Redlock, while a booking is `unknown`? | No. A database constraint allows one active attempt per itinerary item (A12), two editors land on the same attempt, and retries reuse the stored key and body. Only the user resends a POST (A14). A distributed lock would add infrastructure for a guarantee the constraint already gives. |

## Guarantees

Provider mock, proven by tests: `provider-mock/README.md` and `docs/STATUS.md`, Section 2.6.

Application, each proven over real HTTP against real Postgres:

| Guarantee | Proven by |
|---|---|
| A trip survives a hard kill of the application, and a replaced Postgres container through its volume. | `tests/compose_smoke.py` |
| A budget comes back as the exact string it went in as, including `0.00`, after restarts. | `tests/test_app_validation.py` |
| With a decodable body, every route except `GET /health`, `GET /ready` and `GET /openapi.json` refuses a missing token with 401, and every `{tripId}` route refuses a nonparticipant with 404. A route cannot hide from that check. | `tests/test_app_permissions.py` |
| A nonparticipant, a missing trip and a malformed id get the same answer, apart from `requestId`. | `tests/test_app_permissions.py` |
| Only the organizer edits a trip or manages participants. The organizer's own row cannot be added, changed or removed. | `tests/test_app_permissions.py` |
| Invalid input gets an actionable error, never echoes the input, and saves nothing. | `tests/test_app_validation.py` |
| Creating a trip is atomic. A failure after the trip row leaves no trip, destinations or organizer. | `tests/test_app_transactions.py` |
| Of concurrent edits holding the same `version`, exactly one wins. Every other gets a 409 with the current state. | `tests/test_app_transactions.py`, with a trigger that forces the requests to overlap |
| Concurrently adding the same participant creates one row and no 500. | `tests/test_app_transactions.py`, with the same kind of trigger |
| Migrations match the models. The database itself enforces date order, a nonnegative budget, valid roles, its keys and at most one organizer per trip. | `tests/test_app_database.py` |
| Every error body has the provider's `Error` shape, including a 500. | `tests/test_app_errors.py`, and a client hook on every response of 400 or above |
| Every provider response is validated against the contract. A schema-invalid, non-JSON, undeclared or unencodable answer is a failed leg or a 502, shows nothing and saves nothing. | `tests/test_app_provider.py` |
| Search tells complete, partial, empty and failed apart for each destination, and a failed leg keeps the others. With the default timeout, a slow supplier is a warning, not a timeout. | `tests/test_app_provider.py` |
| No price is presented as confirmed unless a confirmed booking covers it: search options are always an `estimate`. | `tests/test_app_provider.py`, `tests/test_app_itinerary.py` |
| The itinerary survives a hard kill, holds the provider's offer exactly as sent, and only editors and the organizer change it. | `tests/test_app_itinerary.py`, `tests/test_app_itinerary_permissions.py`, `tests/compose_smoke.py` |
| Expired quotes, unavailable offers, missing information, party and date mismatches and schedule conflicts are reported, each with a case that must not raise it. | `tests/test_app_itinerary.py` |
| Every row of the outcome table (`docs/CONTRACT_NOTES.md`, 2.8) leaves the state it names, for the first POST and for the retry of an `unknown` attempt, and the attempt keeps its key and reference. | `tests/test_app_booking*.py` |
| The attempt is committed as `unknown` before the POST. A hard kill leaves it, and the poller reconciles by reference with no second POST, or the user retries with the same key and the same body. | `tests/test_app_booking*.py` |
| Polling resolves `pending` to `confirmed` or `failed` with no user action, never turns `unknown` into anything on a 404, never POSTs, waits for a 429's `Retry-After`, resumes after a restart, and stops when `BOOKING_POLL_ENABLED=0`. | `tests/test_app_booking*.py` |
| Of 24 concurrent bookings of one item (the ordinary race), exactly one reaches the provider and one attempt exists. In 12 staged simultaneous inserts, a loser never gets a second POST, even when the winner closes at once. After `confirmed` no other is allowed, and after `rejected` or `failed` a new one gets a new key and reference. | `tests/test_app_booking*.py` |
| Changed terms are refused with no provider call, a quote changed at the provider is `rejected`, and the stored snapshot is untouched. | `tests/test_app_booking*.py` |
| One item per offer, also under concurrent adds. An item with an open or confirmed booking cannot be deleted, and a concurrent delete and book never leave both succeeding or an orphan attempt. | `tests/test_app_booking*.py` |
| A confirmed item stays `confirmed` at the provider's accepted price after the quote changes and the item is refreshed. Mixed outcomes stay visible side by side. | `tests/test_app_booking*.py` |
| Success and error responses carry `X-Request-Id`, an error body's `requestId` equals it, and a provider call logs one line with both request ids. The test checks several 200s and a 404, not a 500. | `tests/test_app_diagnostics.py` |
| Migration `0003` matches the models, and the database itself enforces one open attempt per item, `RESTRICT`, the state check and one item per offer. | `tests/test_app_migration_0003.py` |
| Totals are exact and reproducible, and rounding exists only in `app/money.py`. | `tests/test_app_itinerary.py`, including a scan of `app/` |
| Of concurrent itinerary writes holding the same `version`, exactly one wins. Every other gets a 409 with the current state, also when the item state changed under it. | `tests/test_app_concurrency.py` |
| No database transaction stays open while the provider answers a search, an add or a refresh (booking is not in that test), and `/health` stays responsive under 50 waiting searches. | `tests/test_app_concurrency.py` |
| A trip view never pairs a `version` with another version's destinations. The itinerary view reads the same way, but no test races it yet. | `tests/test_app_transactions.py` |
| `/ready` answers 503 when the database does not, and never depends on the provider. | `tests/test_app_permissions.py` |

## Limits I accept

- **Amounts above `9999999999.99`:** the contract's pattern has no upper bound, but `NUMERIC(12,2)` stops there (A44). A larger amount is valid for the contract and unusable here, so this is our storage limit, not a provider error. Fixtures stay far below it.
- **A JSON body the decoder rejects answers 422 before 401 or 404.** FastAPI parses JSON before dependencies run. `{}`, an empty body and `text/plain` do reach the dependencies first. It reveals nothing about a trip, and authenticating in middleware would only cover the 401.
- **A `version` past 4300 digits answers 422, not 409.** Python's integer digit limit stops it inside `json.loads`. That limit is a denial-of-service guard, so it stays on.
- **Participant role changes are last-write-wins.** Adds are insert-or-conflict, and neither bumps the trip `version`. One organizer per trip makes a conflict unlikely. Add a version to `trip_participants` if two organizers' edits ever need to conflict.
- **The organizer row is protected by the application only.** The database allows at most one organizer per trip, but a direct `UPDATE` can still change that row's role.
- **`GET /openapi.json` is public** and is the one route outside its own schema. FastAPI registers it that way, and the permission walk names it explicitly.
- **`GET /health` is liveness only,** and runs no query. `GET /ready` runs `SELECT 1` and is what the compose healthcheck uses. Neither calls the provider, because the application serves without it: a search with no provider shows every leg failed.
- **Test tokens are a plain dictionary lookup,** which is not constant time. That does not matter for fixed test tokens, and a real identity provider replaces it.
- **`ApiError` carries an HTTP status,** so the domain knows about HTTP. Split it when a non-HTTP caller, the MCP server of Iteration 6, needs the services.
- **Migrations run online only.** Offline SQL scripts are not needed while the deploy runs `upgrade head` itself.
- **One provider client per process, never closed.** The process exit closes its sockets. Add a lifespan hook if the client is ever restarted in-process.
- **Overlaps compare every pair of items, O(n²).** That is fine for tens of items. Sort by start and sweep if an itinerary grows large.
- **One version per trip.** An organizer's edit to the trip makes an editor's pending itinerary edit stale, and the reverse. Each gets a 409 with the current state, at the cost of one more request.
- **A user waits on the provider for at most `PROVIDER_TIMEOUT_SECONDS`, 10 by default, per request.** On add and refresh, one deadline covers both attempts and the wait between them, and a retry that would not fit is not taken. Before this iteration's O7 change, the worst case was about 25 seconds.
- **`expired` is the application's clock against `validUntil`.** It is a hint (A28). Only `POST /bookings` decides, and a booking of an item labeled expired is still sent.
- **Every destination is expected to have an accommodation.** A day trip gets a `DESTINATION_WITHOUT_STAY` it may not need. The rule is stated in the response, among `rules`.
- **Migration `0003` fails on a trip that holds the same offer twice,** which Iteration 3 allowed. Nothing was deployed before it, so no data is rewritten. Resolve such rows by hand first.
- **Deleting an item deletes its `rejected` and `failed` attempts,** so that history is gone. An open or confirmed booking is never deleted. Keep closed attempts, and give them no foreign key to the item, if an audit trail is ever needed.
- **The reconciliation loop is one task per process, waking every second at most.** The claim is safe across processes, but each process polls on its own: with many instances, raise the wake interval or move the loop to a worker. `booking_attempts` has no index on `next_check_at`, because the open attempts are few. Add a partial index on the open states when they are not.
- **An `unknown` attempt is polled for ever,** at the last interval of `BOOKING_POLL_INTERVALS`. No timer closes it, because a by-reference 404 is not proof (A13). Only the user's retry can close it negatively.
- **A 401 from the provider leaves attempts `unknown`** and logs an ERROR line for the operator. The application does not alert beyond its log, and does not stop polling.
- **A click that arrives after the item's previous attempt closed as `failed` or `rejected` starts a new attempt** at the same reviewed terms. Rule 3 of the brief allows it, and it cannot double-book: one attempt is open at a time and none is allowed after `confirmed`. Only the loser of a simultaneous insert is refused, with the winner's attempt and `BOOKING_CONFLICT`.
- **The database pool bounds concurrent booking opens per process.** SQLAlchemy's defaults, 5 connections plus 10 overflow, apply, and requests beyond them wait for a connection.
- **"A crash of one poll never stops the loop" has no test.** The code enforces it with an exception guard around each poll and each attempt, but nothing kills a poll on purpose to prove it.
- **Booking state changes do not bump the trip `version`,** so a version alone does not identify booking states. A document (Iteration 5) must record them itself.
- **Two retries of one `unknown` attempt may run together.** Both send the same key and body, so the provider answers one booking (A8), and the monotonic update keeps the first outcome that moves forward.
- **A search covers at most 20 destinations,** so one request cannot fan out without bound.
- **A provider response is read whole, with no size limit.** The provider is the only caller of this path, and the timeout bounds how long it can stream.
- **The read inside a write transaction stays `READ COMMITTED`.** `REPEATABLE READ` there would turn the guarded `UPDATE`'s zero rows into a serialization error. A refusal judged on that read rechecks the version first, so a concurrent commit still answers 409. Every view the API returns is read in one `REPEATABLE READ` snapshot.
- **The application waits for the provider mock to be healthy before compose starts it.** The application itself does not need the provider to start: without it, every search leg fails with its status.

The mock's limits are in `docs/STATUS.md`, Section 2.6.

## Risks

- **Provider outside the transaction:** an accepted booking can exist while we cannot tell. The attempt committed before the POST and the `unknown` state cover this, not the architecture.
- **One process, one database:** a failure in one domain can affect the others. Accepted at this scale.

## Scaling

The first bottleneck to expect is slow provider calls, not the database. The growth path above moves the worker and the MCP server out first. Nothing here is built for scale yet.
