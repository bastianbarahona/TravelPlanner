# CLAUDE.md — Travel Planning Platform

> Project-specific development harness. Committed as part of the deliverable.
> Scope: this file governs work in this repository only.

## Ground rules

1. **The contract is the truth.** `contracts/openAPI.json` is immutable. Before accepting any schema, model, client or test touching the provider, compare it **field by field** against the contract. If they disagree, the contract wins.
2. **Money is `Decimal`, never `float`.** Database `NUMERIC(12,2)`, Python `Decimal`, JSON string with exactly two decimals matching `^(0|[1-9][0-9]*)\.[0-9]{2}$`. Rounding is `ROUND_HALF_UP`, applied in one place: `app/money.py`.
3. **`revision` is a string**, not an integer. So is every provider identifier.
4. **`unknown` is our state, not the provider's.** Provider outcomes are only `pending | confirmed | failed`. A timeout or 5xx is never proof of rejection.
5. **No feature ships without a test that can fail.** A test that only asserts a canned mock response is not evidence.

## Architecture decisions (already made — do not relitigate)

| Decision | Rejected alternative | Why |
|---|---|---|
| Modular monolith | Microservices | The PRD's guarantees (concurrent edits, booking idempotency, document/reference consistency) are transactional. One Postgres + optimistic locking solves them directly; sagas and outbox would add failure surface for no scale benefit. |
| Optimistic locking (`version` column) | Pessimistic locks / CRDT | Low contention, simple to explain, returns a usable 409 with current state. |
| Offer stored as JSONB snapshot | Local catalog tables | The app must consume the provider through its public contract, not mirror its database. |
| Polling + state endpoint | WebSocket-only push | The PRD requires recovery without assuming every notification arrived. A readable state endpoint is the source of truth. |
| `Decimal` | float | Reproducible totals (US-05). |

## Module layout

```
app/
  api/          FastAPI routers — thin, no business logic
  domain/       entities, rules, invariants
  services/     use cases (trips, search, booking, documents)
  providers/    HTTP client for the external provider (contract-bound)
  mcp/          read-only MCP server and tools
  assistant/    LLM orchestration (swappable, deterministic mode for tests)
  db/           models, migrations, repositories
  money.py      the only place rounding happens
  config.py     environment only; no secrets in code
provider-mock/  separate service implementing openAPI.json
web/            React + TS, minimal
tests/
contracts/openAPI.json   ← never modified
```

## Definition of done (per change)

1. Code written and **read line by line** before committing.
2. At least one test covering a **failure path**, not just the happy path.
3. `docker compose up` still works from clean.
4. If it changes a guarantee, `ARCHITECTURE.md` is updated in the same commit.
5. Commit message states what changed and why in one line.

## CI and pull requests

1. **Every pull request is born with Auto-fix on.** The session that opens it turns it on right away, as part of opening it.
2. **Every CI failure is alerted to the owner and logged** in `docs/STATUS.md`, Section 2.7, with no exceptions.
3. **Every fix to a CI failure needs the owner's review and validation,** even when it is automatic, trivial or for a flaky failure. A fix may be pushed to the pull request so CI can check it, but it stays a proposal until the owner validates it. Auto-merge stays off.

## Scope guardrails — do NOT build

- Currency conversion, payments, cancellations, refunds
- Real identity provider (test identities only)
- Maps, drag-and-drop, live presence, real-time collaborative editing
- Pagination, advanced filters, caching layers, message brokers
- Any abstraction with a single implementation
- Visual polish

If something feels like it needs a new layer, write the reason in `ARCHITECTURE.md` under "Limits I accept" instead of building it.

## AI usage protocol

- Use assistants freely for scaffolding, tests and docs.
- **Never accept provider-facing code without the contract open beside it.**
- Log every case where the assistant was wrong in `AI-USAGE.md`: what it got wrong, how it was detected, what was changed.
- Nothing is committed that cannot be explained line by line in a review.

## Documentation targets

- `README.md` — setup, run, tests, evaluations, proposed deployment
- `ARCHITECTURE.md` — decisions, alternatives, guarantees, limits, risks, scaling
- `AI-USAGE.md` — how AI was used and verified
- `DEMO.md` — main workflow + failure and concurrency scenarios

All repository documentation is written in **English**.
