# Architecture

Decisions, alternatives, guarantees, limits, risks and scaling. This file grows with each iteration. The provider mock is the only code so far, so anything here about the application is a commitment, not built behavior.

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

### Money

**Decision:** no binary floating point anywhere money flows. `Decimal` in the domain, `NUMERIC(12,2)` in Postgres, and a string with exactly two decimals in JSON. Rounding is `ROUND_HALF_UP`, in one place: `app/money.py`.

**Why:** US-05 needs reproducible totals.

**Boundary:** an API field is a `str` with the contract's amount pattern, converted by `app/money.py`. It is not a `Decimal` field. Tested with Pydantic: a `Decimal` field accepts the JSON number `420.1` even with `strict=True`, accepts `"420.10\n"`, and emits `"420"` or `"1E+3"`.

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

Application: none yet. Iteration 2 adds the first ones.

## Limits I accept

- **Amounts above `9999999999.99`:** the contract's pattern has no upper bound, but `NUMERIC(12,2)` stops there (A44). A larger amount is valid for the contract and unusable here, so this is our storage limit, not a provider error. Fixtures stay far below it.

The mock's limits are in `docs/STATUS.md`, Section 2.6.

## Risks

- **Provider outside the transaction:** an accepted booking can exist while we cannot tell. The attempt committed before the POST and the `unknown` state cover this, not the architecture.
- **One process, one database:** a failure in one domain can affect the others. Accepted at this scale.

## Scaling

The first bottleneck to expect is slow provider calls, not the database. The growth path above moves the worker and the MCP server out first. Nothing here is built for scale yet.
