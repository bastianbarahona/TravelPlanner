# Architecture

Decisions, alternatives, guarantees, limits, risks and scaling. This file grows with each iteration. The provider mock and the application core exist. Anything here about a later iteration is a commitment, not built behavior.

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

**Decision:** every protected route refuses in the same order, once its body has been decoded: 401 for a missing or bad token, 404 for a nonparticipant, a missing trip or a malformed id, 403 for a participant without the role, then 422. Trip fields and participants belong to the organizer, who is the creator. Viewers and editors behave alike until Iteration 3 gives editors the itinerary.

**Why:** the 404 hides whether a trip exists, as the provider does. Dependencies sit on the routers, so a new route cannot forget them. A walk over `/openapi.json` covers a new route with no test edit.

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
| With a decodable body, every route except `GET /health` and `GET /openapi.json` refuses a missing token with 401, and every `{tripId}` route refuses a nonparticipant with 404. A route cannot hide from that check. | `tests/test_app_permissions.py` |
| A nonparticipant, a missing trip and a malformed id get the same answer, apart from `requestId`. | `tests/test_app_permissions.py` |
| Only the organizer edits a trip or manages participants. The organizer's own row cannot be added, changed or removed. | `tests/test_app_permissions.py` |
| Invalid input gets an actionable error, never echoes the input, and saves nothing. | `tests/test_app_validation.py` |
| Creating a trip is atomic. A failure after the trip row leaves no trip, destinations or organizer. | `tests/test_app_transactions.py` |
| Of concurrent edits holding the same `version`, exactly one wins. Every other gets a 409 with the current state. | `tests/test_app_transactions.py`, with a trigger that forces the requests to overlap |
| Concurrently adding the same participant creates one row and no 500. | `tests/test_app_transactions.py`, with the same kind of trigger |
| Migrations match the models. The database itself enforces date order, a nonnegative budget, valid roles, its keys and at most one organizer per trip. | `tests/test_app_database.py` |
| Every error body has the provider's `Error` shape, including a 500. | `tests/test_app_errors.py`, and a client hook on every response of 400 or above |

## Limits I accept

- **Amounts above `9999999999.99`:** the contract's pattern has no upper bound, but `NUMERIC(12,2)` stops there (A44). A larger amount is valid for the contract and unusable here, so this is our storage limit, not a provider error. Fixtures stay far below it.
- **A JSON body the decoder rejects answers 422 before 401 or 404.** FastAPI parses JSON before dependencies run. `{}`, an empty body and `text/plain` do reach the dependencies first. It reveals nothing about a trip, and authenticating in middleware would only cover the 401.
- **A `version` past 4300 digits answers 422, not 409.** Python's integer digit limit stops it inside `json.loads`. That limit is a denial-of-service guard, so it stays on.
- **Participant role changes are last-write-wins.** Adds are insert-or-conflict, and neither bumps the trip `version`. One organizer per trip makes a conflict unlikely. Add a version to `trip_participants` if two organizers' edits ever need to conflict.
- **The organizer row is protected by the application only.** The database allows at most one organizer per trip, but a direct `UPDATE` can still change that row's role.
- **`GET /openapi.json` is public** and is the one route outside its own schema. FastAPI registers it that way, and the permission walk names it explicitly.
- **`GET /health` is liveness only.** It runs no query, so it stays green while Postgres is down. Add a `SELECT 1` if compose should wait on Postgres reachability.
- **Test tokens are a plain dictionary lookup,** which is not constant time. That does not matter for fixed test tokens, and a real identity provider replaces it.
- **`ApiError` carries an HTTP status,** so the domain knows about HTTP. Split it when a non-HTTP caller, the MCP server of Iteration 6, needs the services.
- **Migrations run online only.** Offline SQL scripts are not needed while the deploy runs `upgrade head` itself.
- **`app/money.py` has no rounding function yet,** because nothing computes an amount. `ROUND_HALF_UP` lands there when Iteration 3 first needs it.

The mock's limits are in `docs/STATUS.md`, Section 2.6.

## Risks

- **Provider outside the transaction:** an accepted booking can exist while we cannot tell. The attempt committed before the POST and the `unknown` state cover this, not the architecture.
- **One process, one database:** a failure in one domain can affect the others. Accepted at this scale.

## Scaling

The first bottleneck to expect is slow provider calls, not the database. The growth path above moves the worker and the MCP server out first. Nothing here is built for scale yet.
