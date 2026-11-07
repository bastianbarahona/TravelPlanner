# Project status and continuity plan

As of commit `59cf671` on `main`, where CI passes.

**Where we stand:** Iteration 1 is merged. The provider mock runs from `docker compose up`, and CI proves its contract on every push. The architecture decision is closed in favor of the modular monolith, and the team roles and the Codex sandbox are set and tested. No application code exists yet. The next step is Iteration 2, the application core.

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
| CI and pull request rules | Done | `CLAUDE.md`, `.github/workflows/ci.yml` |
| Team roles, Codex sandbox and contract barrier | Done and tested, except the subagents at runtime | `CLAUDE.md`, `AI-USAGE.md` |
| Ponytail (YAGNI) for Claude and Codex | Done | `CLAUDE.md`, `AGENTS.md`, `.claude/settings.json` |
| Architecture decisions | Recorded. Modular monolith confirmed by the owner. | `CLAUDE.md`, `ARCHITECTURE.md` |
| Application data model and API | Planned, not started | Section 2.4 |
| MCP tools and assistant | Not started, approach open (1.4, item 2) | |
| Document generation | Not started | |
| Deployment | Idea only (1.4, item 3) | |

### 1.2 Development

Nothing below exists yet unless marked.

| Deliverable (candidate guide) | State |
|---|---|
| Supplied contract, unchanged | ✓ Committed. `.gitattributes` keeps its bytes stable, and a test pins its hash |
| Development harness | Partial: `CLAUDE.md`, `AGENTS.md`, GitHub Actions, the team protocol, Ponytail and `.claude/agents/` |
| Provider mock | ✓ Merged |
| Backend | Not started |
| Frontend | Not started |
| Read-only MCP layer and assistant | Not started |
| Tests and evaluations | Partial: contract tests for the mock, run by GitHub Actions on every push |
| Sample data and example configuration | Partial: mock fixtures and `.env.example` |
| `AI-USAGE.md` | Started: harness and log |
| `ARCHITECTURE.md` | Started: decisions, open proposals and limits |
| `README.md`, `DEMO.md` | Not started. `provider-mock/README.md` covers the mock |

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
| 1 | Closed on 2026-11-07: modular monolith, justified by the owner's notes and recorded in `ARCHITECTURE.md`. | Nothing |
| 2 | Assistant approach. The local notes suggest a deterministic model like B-Agent, which has not been reviewed against US-11 and US-12. Parked by the owner. | Iteration 6 |
| 3 | Deployment target. The local notes propose Azure Container Apps. Only a proposed approach is required. Parked by the owner. | Iteration 8 |
| 4 | Deadline and expected effort. The candidate guide still says "to be confirmed". | Scope cuts |
| 5 | Whether the recommendations pasted during review came from an AI. `AI-USAGE.md` holds them with their origin marked as open. | Iteration 8 |
| 6 | The owner asked for documentation of each TTL tested, once tests cover them. Today the suite runs one TTL, the 900 second default, with the clock pinned around `validUntil`. The pinnable clock stays test-only. | Nothing yet |
| 7 | The Gemini CLI refuses this account with `IneligibleTierError`. Restoring it is optional, because nothing depends on Gemini. | Nothing |
| 8 | The owner set `working_dir` to `C:/DEV/TravelPlanner`, and a test of the same command proves it keeps Codex out of `C:\DEV` and other projects. A fresh PAL loads it, but each session has its own PAL, cached at start. ⚠ Sessions already open keep `C:\DEV`: this one still wrote there after the owner's restart, which reached another session. `pal_qa` can still write, which `"role_args": ["-s", "read-only"]` would fix. Only the owner changes that file. | Other projects, while old sessions stay open |
| 9 | The complete Codex review of `CONTRACT_NOTES.md` found no contradictions and three wording notes: line 106, line 182 and A44 (`AI-USAGE.md`). Rewording them is the owner's call, because those notes are accepted. | Nothing |

### 1.5 Housekeeping

- **This file is versioned since Iteration 1,** so worktrees can read it. It used to be untracked in the main checkout.
- **Auto-fix has no app-wide default,** so the rule in `CLAUDE.md` is what turns it on. It covers pull requests opened from a Claude Code session, not ones opened by hand.
- **`docs/observaciones.md` is local only.** It is excluded through `.git/info/exclude`, so it has no backup and does not exist on other clones or in worktrees.
- **Cases where AI was wrong live in `AI-USAGE.md`,** which `CLAUDE.md` requires for every new one.

---

## 2. Continuity plan

### 2.1 Working rules for every iteration

1. Start each iteration in a clean context, from the prompt in 2.5.
2. Read the contract and the notes before touching provider-facing code.
3. Resolve the iteration's open design decisions first, in small batches, each with a recommendation.
4. Test a recommendation before accepting it, whether it comes from a person or from an AI.
5. Close by updating this file, then commit only when the owner asks.
6. Follow "CI and pull requests" in `CLAUDE.md`: every pull request is born with Auto-fix on, every CI failure is alerted and logged in 2.7, and every fix needs the owner's review and validation.
7. Follow "Multi-model assistant protocol (PAL)" in `CLAUDE.md`: Codex runs from the CLI with `-s` and never through `clink`, whoever wrote a change does not review it, and mistakes go in `AI-USAGE.md`.
8. Apply Ponytail as `CLAUDE.md` limits it. Before closing an iteration, review `ponytail:` comments with `/ponytail-debt`.

### 2.2 Iterations

| # | Goal | Stories | Done when | State |
|---|---|---|---|---|
| 1 | Repository base and provider mock | Foundation for US-03, US-07, US-08 | `docker compose up` runs the mock, and contract tests pass against it | ✓ Merged (2.6) |
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
| Migration tool and approach to schema changes | 2 |
| Test identity mechanism | 2 |
| Refusal codes, and who edits a trip | 2 |
| Money at the API boundary | 2 |
| Background work mechanism, without a message broker | 4 |
| Document format, file storage, and file-to-record consistency | 5 |
| MCP SDK and transport, LLM provider, deterministic mode (1.4, item 2) | 6 |
| Deployment proposal (1.4, item 3) | 8 |

### 2.4 Next: Iteration 2, application core

**Scope**

- `app/` in the layout of `CLAUDE.md`, with only the modules this iteration needs: `api`, `domain`, `services`, `db`, `money.py` and `config.py`.
- Postgres and the application service in `docker-compose.yml`, next to the mock.
- Test identities.
- Trips (US-01): one or more destinations, dates and a USD budget. Create, list your own, read and edit.
- Participants (US-02): the organizer adds, changes and removes viewers and editors. They are added by test identity, with no invitation flow.
- Permission checks on every route.
- Optimistic locking from the first write route, using the `version` column `CLAUDE.md` decided. A stale trip edit gets a 409 with the current state. Iteration 3 extends this to the itinerary.
- `ARCHITECTURE.md`, extended with the application's first guarantees and limits.
- CI and `tests/compose_smoke.py`, extended to the application and Postgres.

**Out of scope:** the provider client, search, itinerary, bookings, documents, MCP, the frontend, and any change to the mock or the contract.

**Decisions to settle first, with the recommended default**

Already settled: the modular monolith (`ARCHITECTURE.md`).

| # | Decision | Recommendation | Evidence so far |
|---|---|---|---|
| 1 | Migrations | Alembic with SQLAlchemy 2, applied by a one-shot `migrate` service in compose that the application waits for, so instances never race. Forward-only. | Not tested yet |
| 2 | Test identities | Seeded users, each with a fixed bearer token. The token identifies the user, and no client-supplied user id is trusted. The same credential can later reach status subscriptions and MCP tools (US-02, US-12). | Not tested yet |
| 3 | Refusals and roles | No or bad token: 401. Nonparticipant: 404, so a trip's existence never leaks, as the provider does (`:461`). Participant without the role: 403. Trip fields and participants belong to the organizer, because the PRD gives editors the itinerary, so editors and viewers behave alike until Iteration 3. One organizer per trip: the creator. | Tested: a permission dependency runs before schema and query validation. See the limit below. |
| 4 | Money at the API | A `str` field with the contract's amount pattern, converted through `app/money.py`. Never a `Decimal` field. | Tested: `Decimal` accepts the JSON number `420.1` even with `strict=True`, accepts `"420.10\n"`, and emits `"420"` or `"1E+3"`. The pattern rejects all of them. |

⚠ **Limit to accept or fix:** FastAPI parses JSON before running dependencies, so a malformed body gets 422 before 401 or 404. Tested. It reveals nothing about a trip, so the recommendation is to record it under "Limits I accept" in `ARCHITECTURE.md`. Authenticating in middleware is the alternative, and it would only cover the 401.

**Rules from `CONTRACT_NOTES.md` that reach this iteration**

- A32: trim destinations, reject them if nothing is left, and keep the user's casing.
- A36: refuse a past start date when trip dates are set, but not on other edits, so a trip under way stays editable. Compare against the UTC date minus one day.
- A44: the amount limit of `NUMERIC(12,2)` applies to budgets too, so `9999999999.99` is the maximum. `0.00` is valid, and `Decimal("0.00")` is falsy.

**The team in this iteration**

- Director: Claude Code on Opus at high effort, which is the session itself.
- First check: the subagents load with their model and effort, Codex answers through the read-only command, a fresh PAL starts `clink` in `C:\DEV\TravelPlanner`, and Ponytail is active. The first three have not been proven live yet (`AI-USAGE.md`). If one fails, report it before relying on it.
- Dev: `dev-principal`. Large work may go to Codex with `workspace-write`, given `app/` and `tests/` only, so `contracts/` stays out of reach. `app/` must exist first, and `git diff` is reviewed afterwards.
- QA: `qa-lead`. Whoever wrote a change does not review it, so run the read-only Codex review on Claude's code, covering the permission, money and `version` rules.

**Tests that must exist, and must be able to fail**

As in Iteration 1, they run over real HTTP with the application as a real process. They use real Postgres, because version checks, `NUMERIC` and constraints are what is under test, so SQLite cannot stand in.

1. A trip survives a hard kill of the application, and a replaced Postgres container through its volume.
2. Invalid input returns an actionable error and saves nothing: no destination, a blank one, `endsOn` before `startsOn`, a past start date, and each bad budget of decision 4.
3. A budget round-trips exactly: `420.10` and `0.00` come back as the same strings after a restart.
4. A walk over `/openapi.json` proves every route refuses a missing token with 401 and a nonparticipant with 404. A new route is covered with no test edit, and only an explicit list of public routes is exempt. The walk sends `{}`, because a malformed body answers 422 first.
5. A role matrix over every write route. Viewers and editors cannot edit the trip or manage participants.
6. For a nonparticipant, an existing trip and a missing one give the same answer, apart from `requestId`.
7. Creating a trip is atomic: a forced failure after the trip row leaves no trip, destinations or organizer.
8. A stale `version` gets a 409 with the current state. Concurrent edits with the same `version` produce exactly one success.
9. Concurrently adding the same participant creates one row and no 500.
10. `alembic upgrade head` works on an empty database, and `alembic check` finds no drift between models and migrations.
11. Every error body has the application's error shape, including validation errors and the malformed-body case.

**Done when:** all of the above pass, `tests/mutation_check.py` also covers the permission, money and `version` rules with every mutation killed, `docker compose up` works from clean, CI is green, and this file is updated.

### 2.5 Starter prompt for the next iteration

Before pasting it, make sure this plan is on `main` and that the main checkout has pulled it, because a new session starts from that code. Start the session on Opus at high effort, because it acts as the Director.

```text
Read CLAUDE.md, ARCHITECTURE.md, docs/STATUS.md and docs/prd.md completely before
writing anything. From docs/CONTRACT_NOTES.md, read Section 1.0 and the entries
A32, A36 and A44.

We are starting Iteration 2 as defined in docs/STATUS.md, Section 2.4. You are the
Director.

First, run the first check in Section 2.4 and report it. Then list the decisions in Section 2.4 with your
recommendation, testing what has not been tested yet, and wait for my answers. Then
implement only that scope, with the roles in CLAUDE.md. Every test must be able to fail.

Do not start Iteration 3. When done, update docs/STATUS.md and show the test results.
Do not commit until I ask.
```

### 2.6 Iteration 1 record

Merged into `main` as `59cf671`, through pull request #1.

**What exists:** `provider-mock/` (service, fixtures, Dockerfile, README), `tests/`, `.github/workflows/ci.yml`, `docker-compose.yml`, `.env.example`, `.gitignore`, `.gitattributes` and `.dockerignore`.

**Decisions taken by the owner**

| Decision | Outcome |
|---|---|
| Mock storage | SQLite on a named Docker volume, in WAL mode. Every operation runs in a `BEGIN IMMEDIATE` transaction. |
| Faults and changes in tests | Fixtures cover what is fixed: the outcome per offer, and destinations with a failing supplier. A control interface outside `/v1`, off unless `MOCK_CONTROL_ENABLED=1`, covers what changes over time. |
| Test runner | `pytest` over real HTTP, with the mock as a real process on a free port. |
| Expiry boundary | Pinnable mock clock in the control interface, which `:7` of the contract allows. A46 was rewritten to match. |
| Where this file lives | In git. |

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
| `python tests/mutation_check.py` | 72 of 72 mutations killed, in 32 minutes. The first run killed 70, and its two survivors produced two new tests (`AI-USAGE.md`). |
| `docker compose up` from clean, image built without cache | Healthy, running as a non-root user, with the control interface answering 404 |
| `python tests/compose_smoke.py` | Passes: a replay after a kill and in a new container is byte-identical to the first response. Without the volume mount it fails. |
| GitHub Actions, `.github/workflows/ci.yml` | Runs the contract tests and the compose smoke on every push. Both passed on Linux from a fresh checkout, at `d20a00c`. The mutation check stays manual, because it takes 30 minutes. |
| `docker compose stop` | Clean, in under 2 seconds |
| Image built from files with CRLF line endings | Builds and serves. This stands in for a clean Windows clone, which needs a commit first. |
| `contracts/openAPI.json` | SHA-256 equal to the committed blob: `9bf9b93e…` |

**Limits accepted**

- The fault and the pinned clock live in memory, so a mock restart clears them. Offer changes are stored.
- Every read takes the SQLite write lock, because a read may settle a booking or refresh a quote. One writer at a time is enough for a mock.
- `Retry-After` is a fixed 2 seconds, and rate limiting exists only as a fixture or a control fault. The mock has no real limiter.
- `test_suppliers_are_queried_concurrently_within_one_deadline` measures wall time, so a heavily loaded machine could fail it.

### 2.7 CI failure log

Rule 6 of 2.1 applies. Newest first. A fix stays a proposal until the last column names the owner's validation.

| Date | Commit | Job | Cause | Fix | Validated by owner |
|---|---|---|---|---|---|
| | | | No failures so far | | |
