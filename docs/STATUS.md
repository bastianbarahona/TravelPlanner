# Project status and continuity plan

Iteration 1 is committed on branch `claude/iteration-1-setup-4f5751`, on top of commit `9699b41`. It is not merged into `main` yet.

**Where we stand:** the provider mock runs from `docker compose up` and passes its contract tests. No application code exists yet. The next step is Iteration 2, the application core.

---

## 1. Status

### 1.1 Design

| Area | State | Where |
|---|---|---|
| Provider contract analysis | Done | `docs/CONTRACT_NOTES.md` |
| Contract ambiguities | Done: all 46 reviewed and accepted by the owner. A46 was revised in Iteration 1 (2.6). | `docs/CONTRACT_NOTES.md`, Section 4 |
| Booking lifecycle and reconciliation | Done at contract level | `docs/CONTRACT_NOTES.md`, Section 2 |
| Error handling per code | Done at contract level | `docs/CONTRACT_NOTES.md`, Section 3 |
| Provider mock: storage, fixtures, test controls | Done | `provider-mock/README.md` |
| Architecture decisions | Recorded, with one open challenge (1.4, item 1) | `CLAUDE.md` |
| Application data model and API | Not started | |
| MCP tools and assistant | Not started, approach open (1.4, item 2) | |
| Document generation | Not started | |
| Deployment | Idea only (1.4, item 3) | |

### 1.2 Development

Nothing below exists yet unless marked.

| Deliverable (candidate guide) | State |
|---|---|
| Supplied contract, unchanged | ✓ Committed. `.gitattributes` keeps its bytes stable, and a test pins its hash |
| Development harness | Partial: `CLAUDE.md` committed |
| Provider mock | ✓ Done |
| Backend | Not started |
| Frontend | Not started |
| Read-only MCP layer and assistant | Not started |
| Tests and evaluations | Partial: contract tests for the mock |
| Sample data and example configuration | Partial: mock fixtures and `.env.example` |
| `README.md`, `ARCHITECTURE.md`, `DEMO.md`, `AI-USAGE.md` | Not started. `provider-mock/README.md` covers the mock |

User stories implemented: 0 of 12. The mock is the foundation for US-03, US-07 and US-08.

### 1.3 Decisions that carry the most weight

The notes hold the detail. These are the ones an implementation most easily breaks.

| ID | Rule |
|---|---|
| A1 | A 503 on `POST /bookings` means `unknown`, never `failed` or `rejected`. |
| A4 | The mock checks availability before revision, price and expiry. |
| A12 | One active attempt per itinerary item, enforced by a database constraint. |
| A13 | A by-reference 404 is not proof. Only a same-key retry can close `unknown` negatively. |
| A14 | Only the user resends a POST. Background work only reads. |
| A15 | No timeout changes a `pending` booking. The UI releases the user after 60 to 90 seconds. |
| A25 | Offer refresh happens before the user confirms, never between confirmation and the POST. |
| A38 | The application is the provider's only caller, with one stable token. |
| A41 | Every mock error body is an `Error`. FastAPI's default 422 must never appear. |
| A45 | The client parses strictly (`CONTRACT_NOTES.md`, Section 1.0). A schema-invalid `2xx` on POST means `unknown`. |
| A46 | Mock time can be pinned from tests. Only that proves the exact `validUntil` boundary. |

### 1.4 Open items for the owner

| # | Item | Blocks |
|---|---|---|
| 1 | Modular monolith or microservices. The local notes lean to microservices, while `CLAUDE.md` records the monolith as decided. Parked by the owner. | Iteration 2 |
| 2 | Assistant approach. The local notes suggest a deterministic model like B-Agent, which has not been reviewed against US-11 and US-12. Parked by the owner. | Iteration 6 |
| 3 | Deployment target. The local notes propose Azure Container Apps. Only a proposed approach is required. Parked by the owner. | Iteration 8 |
| 4 | Deadline and expected effort. The candidate guide still says "to be confirmed". | Scope cuts |
| 5 | Whether the recommendations pasted during review came from an AI. If so, they belong in `AI-USAGE.md` (1.5). | Iteration 8 |
| 6 | The owner asked for documentation of each TTL tested, once tests cover them. Today the suite runs one TTL, the 900 second default, with the clock pinned around `validUntil`. The pinnable clock stays test-only. | Nothing yet |

### 1.5 Housekeeping

- **This file moved into git with Iteration 1.** It used to be untracked in the main checkout, so worktrees could not see it. ⚠ Delete that untracked copy before merging the branch, or git refuses the merge.
- **`docs/observaciones.md` is local only.** It is excluded through `.git/info/exclude`, so it has no backup and does not exist on other clones or in worktrees.
- **Material for `AI-USAGE.md`,** all detected by testing or by reading the contract:
  - `Path(max_length=128)` answers 422, not 404.
  - `Optional[str]` accepts an explicit `null`.
  - `offers: []` alone does not mean "no results", because `partial` may be true.
  - A refresh right before the POST bypasses the user's review of the terms.
  - A unique index on attempts would burn rejected references.
  - A generic Retry on a `failed` booking only replays `failed`.
  - `FormatChecker` skips `date-time` without `rfc3339-validator`.
  - The first versions of A12 and A25 in the notes were imprecise, and review corrected them.
  - A46 said no controllable clock was needed, which made required test 8 impossible to prove. Detected by comparing the note with the test list.
  - A plain SQLite `BEGIN` failed 620 of 640 concurrent requests with `database is locked`. Detected by a spike before the storage decision, and `BEGIN IMMEDIATE` fixed it.
  - The first test suite let an invalid fixture through, because no test read that offer, and let a failed supplier's offers through, because no failed supplier had any. The first was detected by mutation, the second while writing the mutations.
  - The `UPDATE` that stores a resolved booking looked redundant, because a read recomputes the same state. Mutation exposed it: without it, a clock stepping back reverses a terminal outcome. It stayed, and it now has a test.
  - An assistant-written test helper contained a dead `if False` expression. Detected by reading the code before running the mutations.

---

## 2. Continuity plan

### 2.1 Working rules for every iteration

1. Start each iteration in a clean context, from the prompt in 2.5.
2. Read the contract and the notes before touching provider-facing code.
3. Resolve the iteration's open design decisions first, in small batches, each with a recommendation.
4. Test a recommendation before accepting it, whether it comes from a person or from an AI.
5. Close by updating this file, then commit only when the owner asks.

### 2.2 Iterations

| # | Goal | Stories | Done when | State |
|---|---|---|---|---|
| 1 | Repository base and provider mock | Foundation for US-03, US-07, US-08 | `docker compose up` runs the mock, and contract tests pass against it | ✓ Done (2.6) |
| 2 | Application core: persistence, test identities, trips, permissions | US-01, US-02 | Trips survive a restart, and nonparticipants are refused on every route | Next |
| 3 | Provider client, search, itinerary, budget, concurrent edits | US-03, US-04, US-05, US-06 | Partial, empty and failed searches differ, and a stale edit returns a usable 409 | |
| 4 | Booking and reconciliation | US-07, US-08 | Every row of `CONTRACT_NOTES.md` 2.8 has a test, including a crash after commit | |
| 5 | Documents | US-09, US-10 | No missing or partial file is ever reported as ready | |
| 6 | MCP layer, assistant and evaluations | US-11, US-12 | Tools enforce the acting user's permissions, and deterministic evaluations run without a model | |
| 7 | Minimal frontend | All workflows | Every required workflow and failure state can be completed in the UI | |
| 8 | Delivery | Guide deliverables | A clean clone runs from the README, and the demo scenarios reproduce | |

### 2.3 Design decisions still to make

| Decision | Iteration |
|---|---|
| Application architecture (1.4, item 1) | 2 |
| Migration tool and approach to schema changes | 2 |
| Test identity mechanism | 2 |
| Background work mechanism, without a message broker | 4 |
| Document format, file storage, and file-to-record consistency | 5 |
| MCP SDK and transport, LLM provider, deterministic mode (1.4, item 2) | 6 |
| Deployment proposal (1.4, item 3) | 8 |

### 2.4 Next: Iteration 2, application core

**Scope**

- Application service with its own Postgres database, added to `docker-compose.yml`.
- Test identities, trips and participants with their roles.
- Permission checks on every route.

**Out of scope:** the provider client, search, itinerary, bookings, documents, MCP and the frontend.

**Decisions to settle first:** the three listed for Iteration 2 in 2.3. Item 1 of 1.4 is parked by the owner and blocks this iteration, so raise it first. Bring a recommendation for each, tested where it can be.

**Done when:** trips survive a restart, nonparticipants are refused on every route, `docker compose up` still works from clean, and this file is updated.

### 2.5 Starter prompt for the next iteration

```text
Read CLAUDE.md, docs/STATUS.md, docs/CONTRACT_NOTES.md and contracts/openAPI.json
completely before writing anything.

We are starting Iteration 2 as defined in docs/STATUS.md, Section 2.4.

First, list the decisions in Section 2.4 with your recommendation and wait for my
answers. Then implement only that scope. Every test must be able to fail.

Do not start Iteration 3. When done, update docs/STATUS.md and show the test results.
Do not commit until I ask.
```

### 2.6 Iteration 1 record

**What exists:** `provider-mock/` (service, fixtures, Dockerfile, README), `tests/`, `docker-compose.yml`, `.env.example`, `.gitignore`, `.gitattributes` and `.dockerignore`.

**Decisions taken by the owner**

| Decision | Outcome |
|---|---|
| Mock storage | SQLite on a named Docker volume, in WAL mode. Every operation runs in a `BEGIN IMMEDIATE` transaction. |
| Faults and changes in tests | Fixtures cover what is fixed: the outcome per offer, and destinations with a failing supplier. A control interface outside `/v1`, off unless `MOCK_CONTROL_ENABLED=1`, covers what changes over time. |
| Test runner | `pytest` over real HTTP, with the mock as a real process on a free port. |
| Expiry boundary | Pinnable mock clock in the control interface, which `:7` of the contract allows. A46 was rewritten to match. |
| Where this file lives | In git, on the iteration branch. |

**Implementation choices worth defending**

- Request bodies are validated against `contracts/openAPI.json` at runtime. The mock has no second copy of the schemas, and handlers take the raw request, so FastAPI's 422 cannot occur.
- A pending booking resolves on read, not through a background task, so the transition survives a restart. The resolved state is stored, so a terminal outcome cannot reverse.
- Revisions are random, not counters. A revision is never reused, even after a fixture reset, which A13 relies on.
- An idempotency key and a reference live on the booking row, because only accepted requests record them (A6, A7). `UNIQUE (caller, ...)` constraints back the checks.
- `GET /v1/bookings/by-reference/` uses a `:path` route, because the server decodes `%2F` before routing and a reference may contain a slash.

**Required tests**

`provider-mock/README.md` holds the same numbered list, which `tests/mutation_check.py` uses.

Every response, in every test, goes through `tests/contract.py`, using the client hook in `tests/support.py`. That covers schema, formats, prose rules and `Retry-After`.

| # | Required test | Where |
|---|---|---|
| 1 | Every response validates against the contract | The hook, plus `tests/test_contract_checks.py`, which proves the checks detect violations, and `test_every_fixture_offer_satisfies_the_contract` |
| 2 | Each prose-only rule has its own assertion | `tests/contract.py`, driven by `test_bookings.py`, `test_offers.py` and `test_errors.py` |
| 3 | Replay returns `200` with the current state | `test_bookings.py`, "replay" tests |
| 4 | Concurrent requests produce one `201` and one booking | `test_bookings.py`, "concurrent" tests, 24 requests released together |
| 5 | All four 409 cases, in order | `test_bookings.py`, one test per pair of adjacent steps in 2.1 |
| 6 | Keys, references and bookings survive a restart | `test_durability.py`, which hard-kills the process |
| 7 | Caller isolation | `test_bookings.py`, "callers" tests |
| 8 | Acceptance exactly at `validUntil` is rejected | `test_bookings.py`, with the clock pinned one microsecond before, exactly at, and after |
| 9 | Partial, complete, empty and failed searches | `test_offers.py` |
| 10 | Invalid input always returns an `Error` | `test_errors.py` |

**Evidence**

| Check | Result |
|---|---|
| `python -m pytest tests` | 234 passed, in about 50 seconds |
| Responses declared in the contract and exercised | 28 of 28 |
| `python tests/mutation_check.py` | 72 of 72 mutations killed, in 32 minutes. The first run killed 70, and its two survivors produced two new tests (1.5). |
| `docker compose up` from clean, image built without cache | Healthy, running as a non-root user, with the control interface answering 404 |
| Booking after `docker compose restart` and after `docker compose kill` | Byte-identical to the first response |
| `docker compose stop` | Clean, in under 2 seconds |
| Image built from files with CRLF line endings | Builds and serves. This stands in for a clean Windows clone, which needs a commit first. |
| `contracts/openAPI.json` | SHA-256 equal to the committed blob: `9bf9b93e…` |

**Limits accepted**

- The fault and the pinned clock live in memory, so a mock restart clears them. Offer changes are stored.
- Every read takes the SQLite write lock, because a read may settle a booking or refresh a quote. One writer at a time is enough for a mock.
- `Retry-After` is a fixed 2 seconds, and rate limiting exists only as a fixture or a control fault. The mock has no real limiter.
- `test_suppliers_are_queried_concurrently_within_one_deadline` measures wall time, so a heavily loaded machine could fail it.
