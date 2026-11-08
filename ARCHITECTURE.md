# Architecture

Decisions, alternatives, guarantees, limits, risks and scaling. This file grows with each iteration. The provider mock, the application core, the provider client, search, the itinerary, the budget and documents exist. Anything here about a later iteration is a commitment, not built behavior.

The table of decisions in `CLAUDE.md` is the short version. This file holds the reasoning.

## Decisions

### Modular monolith

**Decision:** one application in Docker Compose, with one Postgres. Confirmed by the owner on 2026-11-07. Microservices were rejected.

**Why:** the PRD's strongest demands need immediate transactional consistency: concurrent edits (US-06), recovery from partial bookings (US-08) and immutable history. Microservices would need sagas and a transactional outbox, adding failure surface with no scale benefit at this stage. The candidate guide accepts either, and says more processes earn nothing.

**What this does not buy:** the provider sits outside every transaction, so a monolith does not make a booking atomic. US-08 rests on committing the attempt before the POST, and on reconciliation (`docs/CONTRACT_NOTES.md`, 2.7 and 2.9). Those are needed in either architecture.

**Growth path:** domains stay separated in code, so the document worker and the MCP server can run as separate processes from the same codebase, then move to their own containers if traffic justifies it. Whether they start that way is decided in Iterations 4 and 6.

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

### Party size

**Decision:** `trips.travelers`, from 1 to 20, required on create and changed by the organizer. The provider requires a party on every search, and searching Lisbon for 3 travelers answered an empty result, which reads like "no options". The migration gives existing trips 1 traveler.

### Estimates and confirmed prices

**Decision:** `priceKind` is derived, not stored. One function returns `estimate` today, and Iteration 4 adds `confirmed` there for an item covered by a confirmed booking, whose accepted price will be `Booking.offer.price`. The budget reports `estimatedUsd`, `confirmedUsd` and their total, so the distinction needs no new meaning for any column.

**Rounding:** totals are exact `Decimal` sums of two-decimal prices, so nothing rounds today. `format_amount` quantizes half up all the same. Before this iteration it formatted with `:.2f`, which rounds half even and turned `0.125` into `0.12`. A source scan fails on any other rounding primitive in `app/`.

### Documents

**Decision:** a document is an HTML file built with the standard library from a source captured when it was requested, and generated later by a worker. Confirmed by the owner on 2026-11-07. PDF was left out, and no dependency was added.

**Format:** every string from a user or the provider goes through `html.escape(..., quote=True)`. The file has no script and no external resource, and it downloads with `content-security-policy: default-src 'none'` and `x-content-type-options: nosniff`, so a hostile destination name stays text.

**Storage and consistency:** the content is a `bytea` column in the document row, so a file and its reference commit in one transaction and cannot diverge. The database refuses a `complete` row without content (`ck_documents_complete`) and content whose SHA-256 is not `content_sha256` (`ck_documents_hash`). A download re-hashes the bytes anyway: a mismatch sets the document `failed` with a fenced `UPDATE`, sends no bytes, and answers 409 `DOCUMENT_CORRUPT`. A retry regenerates it, and the same source and `requested_at` render the same bytes, so the original hash comes back.

**Snapshot (US-10):** the source is read when the document is requested, in one `REPEATABLE READ` snapshot: the trip, its items in position order and each item's booking state. It is stored as JSONB, and the worker only renders it, so a later edit never changes a document, and the file says which `Trip version` it shows. It holds nothing that depends on the clock or the caller, so two users asking for the same trip state hash to the same `source_sha256`. `UNIQUE (trip_id, source_sha256)` makes a repeated request return the stored document. The `INSERT ... ON CONFLICT DO NOTHING` runs after the snapshot ends: inside `REPEATABLE READ`, 11 of 12 concurrent inserts failed with serialization errors, and outside it 24 of 24 succeeded with one row.

**Jobs:** `waiting`, `in_progress`, `complete`, `failed`. A worker claims one document per statement with `FOR UPDATE SKIP LOCKED`, takes a lease with a fresh token, and increments `attempts`. Every write that ends a claim (done, back to `waiting`, `failed`) has `WHERE lease_token = :token AND state = 'in_progress'`, so a worker whose lease expired and was taken over drops its result instead of overwriting the new owner's. An expired lease makes the document claimable again, which is how a killed process recovers. A claim that brings `attempts` above 3 ends `failed`, and so does a third render failure. No transaction is open while the worker renders or sleeps: each step is its own short transaction in the thread pool. A failed document is retried by the user, which resets `attempts`.

**Booking states are a stub:** Iteration 4 is built in parallel and merges first. `booking_states` returns `{}` until then, and the merge replaces its body with the latest `booking_attempts` row per item, read in the same snapshot. Migration `0004` points at `0002` and changes to `0003` at that merge.

## Proposals recorded, not adopted

From the owner's notes. They belong to later iterations and stay open until then.

| Proposal | Decide in | What the review found |
|---|---|---|
| The assistant reads trips only through read-only SQL views, such as `v_mcp_trip_itinerary`, with the user filter added in Python | Iteration 6 | Postgres views take no parameters, so the filter lives in Python either way, and forgetting it leaks data. The view adds no permission check. What blocks mutations is a read-only database role, and the MCP layer exposing no write tools. "Only through views" also cannot cover option search, which US-12 requires and which comes from the provider. The likely simpler shape is MCP tools calling the same `services/` and the same permission check as the API. |
| Separate `local_status` and `external_status` on a booking | Iteration 4 | The separation matches US-08. Two details need correcting. The reference cannot be one immutable UUID: each attempt has its own key and reference (A10, and 2.7 rules 4 and 5). Passive reconciliation alone cannot close `unknown` negatively, because a by-reference 404 is not proof, and only a same-key retry by the user can do that (A13, A14). |

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
| No price is presented as confirmed: every option and item is an `estimate`, and the confirmed total is `0.00`. | `tests/test_app_provider.py`, `tests/test_app_itinerary.py` |
| The itinerary survives a hard kill, holds the provider's offer exactly as sent, and only editors and the organizer change it. | `tests/test_app_itinerary.py`, `tests/test_app_itinerary_permissions.py`, `tests/compose_smoke.py` |
| Expired quotes, unavailable offers, missing information, party and date mismatches and schedule conflicts are reported, each with a case that must not raise it. | `tests/test_app_itinerary.py` |
| Totals are exact and reproducible, and rounding exists only in `app/money.py`. | `tests/test_app_itinerary.py`, including a scan of `app/` |
| Of concurrent itinerary writes holding the same `version`, exactly one wins. Every other gets a 409 with the current state, also when the item state changed under it. | `tests/test_app_concurrency.py` |
| No database transaction stays open while the provider answers, and `/health` stays responsive under 50 waiting searches. | `tests/test_app_concurrency.py` |
| A trip view never pairs a `version` with another version's destinations. The itinerary view reads the same way, but no test races it yet. | `tests/test_app_transactions.py` |
| `/ready` answers 503 when the database does not, and never depends on the provider. | `tests/test_app_permissions.py` |
| A document is a point-in-time copy: later edits leave its bytes, `Trip version` and `tripVersion` unchanged, and a new request creates another document. | `tests/test_app_documents.py` |
| Repeated and concurrent identical requests give one document: 24 concurrent `POST`s make one row, one `202` and the rest `200`, never a 500. | `tests/test_app_documents.py` |
| A document is `complete` only with content whose SHA-256 is its record. The database refuses anything else, and a download that finds a mismatch sends no bytes, marks it `failed`, and a retry restores the original hash. | `tests/test_app_documents.py` |
| A document survives a hard kill of the application and finishes after a restart. A worker that lost its lease cannot overwrite the new owner. A document that cannot render ends `failed` after 3 attempts, never `complete`. | `tests/test_app_documents.py` |
| Viewers list, read and download documents. Only editors and the organizer request or retry. Every document route refuses like the rest. | `tests/test_app_documents.py`, `tests/test_app_permissions.py` |
| A document's source pairs a `version` with that version's items: an edit committed in the middle of a capture does not tear it. | `tests/test_app_documents.py`, with a test that holds `LOCK TABLE itinerary_items` while the capture waits, then commits an item and a version bump |

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
- **`expired` is the application's clock against `validUntil`.** It is a hint (A28). Only `POST /bookings`, in Iteration 4, decides.
- **Every destination is expected to have an accommodation.** A day trip gets a `DESTINATION_WITHOUT_STAY` it may not need. The rule is stated in the response, among `rules`.
- **The same offer may be added twice.** Both copies count toward the budget, and the second is reported as a `SCHEDULE_OVERLAP` with the first. Add a uniqueness rule if Iteration 4 needs one item per offer.
- **A search covers at most 20 destinations,** so one request cannot fan out without bound.
- **A provider response is read whole, with no size limit.** The provider is the only caller of this path, and the timeout bounds how long it can stream.
- **The read inside a write transaction stays `READ COMMITTED`.** `REPEATABLE READ` there would turn the guarded `UPDATE`'s zero rows into a serialization error. A refusal judged on that read rechecks the version first, so a concurrent commit still answers 409. Every view the API returns is read in one `REPEATABLE READ` snapshot.
- **The application waits for the provider mock to be healthy before compose starts it.** The application itself does not need the provider to start: without it, every search leg fails with its status.
- **A document's content lives in Postgres.** That keeps the file and its record in one transaction, and it is fine for HTML of kilobytes. Large or binary files would need object storage and a reconciliation of the two.
- **`DOCUMENT_RENDER_DELAY_SECONDS` is a test-only knob.** It exists so a test can see `in_progress`, kill the process mid-generation and take a lease. Leave it at 0 everywhere else.
- **One worker loop per process, polling every 0.5 s.** `SKIP LOCKED` lets several processes share the queue without double work. Add `LISTEN/NOTIFY` if idle load or latency matters. The lease is a fixed `DOCUMENT_LEASE_SECONDS`, and a render slower than that would be claimed twice, which the fence makes harmless but wasteful.
- **Documents are never deleted or expired,** and a trip with many edits can collect many documents. A list is not paginated, like every other list here.
- **A document shows booking states as of its capture.** A booking that settles later does not change an existing document: request a new one.
- **A `POST` or retry answers with the view read after its commit.** With the worker on, a `202` can already say `in_progress`.
- **Two simultaneous downloads of a corrupt file both answer `DOCUMENT_CORRUPT`.** The second one's `details.current` is whatever it reads then. No bytes are ever sent.
- **The corrupt-download `UPDATE` is fenced on `state = 'complete'` and on the hash it read,** but rendering is deterministic, so a regenerated document has the same hash, and only the state conjunct really fences.
- **Not covered by the suite:** the worker loop surviving a database outage, `SKIP LOCKED` across several processes (probed only: 4 workers drained 200 jobs, each claimed once), and the cancellation of the worker on shutdown (tests use hard kills).

The mock's limits are in `docs/STATUS.md`, Section 2.6.

## Risks

- **Provider outside the transaction:** an accepted booking can exist while we cannot tell. The attempt committed before the POST and the `unknown` state cover this, not the architecture.
- **One process, one database:** a failure in one domain can affect the others. Accepted at this scale.

## Scaling

The first bottleneck to expect is slow provider calls, not the database. The growth path above moves the worker and the MCP server out first. Nothing here is built for scale yet.
