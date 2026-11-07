# Project status and continuity plan

As of branch `claude/iteration-2-director-089f39`, on top of `7fcd2f3`. It has not been pushed, so CI has not run on it.

**Where we stand:** Iteration 2 is built and verified on this machine, and committed on its branch. It has not been pushed, so CI is unproven. The API covers trips, participants, permissions and optimistic locking on Postgres, next to the provider mock. The next step is a push, a pull request and green CI. Iteration 3 comes after that.

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
| Team roles, Codex sandbox and contract barrier | Done. Subagent models are confirmed by self-report only, and their effort is not visible | `CLAUDE.md`, `AI-USAGE.md` |
| Ponytail (YAGNI) for Claude and Codex | Done | `CLAUDE.md`, `AGENTS.md`, `.claude/settings.json` |
| Architecture decisions | Recorded. Modular monolith confirmed by the owner. | `CLAUDE.md`, `ARCHITECTURE.md` |
| Application data model and API | Done for trips and participants | `ARCHITECTURE.md`, and Section 2.8 of this file |
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
| Backend | Partial: trips, participants, permissions and persistence, on its branch and not merged |
| Frontend | Not started |
| Read-only MCP layer and assistant | Not started |
| Tests and evaluations | Partial: contract tests for the mock, run by GitHub Actions on every push, and 82 application tests that CI has not run yet |
| Sample data and example configuration | Partial: mock fixtures and `.env.example` |
| `AI-USAGE.md` | Started: harness and log |
| `ARCHITECTURE.md` | Started: decisions, open proposals, guarantees and limits |
| `README.md`, `DEMO.md` | Not started. `provider-mock/README.md` covers the mock |

User stories implemented: 1 of 12, US-01, at API level with no interface yet. US-02 is partial: roles and permissions cover the trip and participant routes, and its planning, document, status and assistant permissions arrive with those features. The mock is the foundation for US-03, US-07 and US-08.

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
| 10 | Iteration 2 is committed on its branch but not pushed, so CI has not run on it (2.8). | Iteration 3 |
| 11 | `clink` in a fresh PAL stays unproven: the permission classifier denied the probe (`AI-USAGE.md`). Nothing depends on it, because `CLAUDE.md` forbids `clink` here. | Nothing |

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
7. Follow "Multi-model assistant protocol (PAL)" in `CLAUDE.md`: Codex runs from the CLI with `-s` and `< /dev/null`, never through `clink`, whoever wrote a change does not review it, and mistakes go in `AI-USAGE.md`.
8. Apply Ponytail as `CLAUDE.md` limits it. Before closing an iteration, review `ponytail:` comments with `/ponytail-debt`.

### 2.2 Iterations

| # | Goal | Stories | Done when | State |
|---|---|---|---|---|
| 1 | Repository base and provider mock | Foundation for US-03, US-07, US-08 | `docker compose up` runs the mock, and contract tests pass against it | ✓ Merged (2.6) |
| 2 | Application core: persistence, test identities, trips, permissions | US-01, US-02 | Trips survive a restart, and nonparticipants are refused on every route | Committed on its branch, waiting for a push and CI (2.8) |
| 3 | Provider client, search, itinerary, budget, concurrent edits | US-03, US-04, US-05, US-06 | Partial, empty and failed searches differ, and a stale edit returns a usable 409 | Next |
| 4 | Booking and reconciliation | US-07, US-08 | Every row of `CONTRACT_NOTES.md` 2.8 has a test, including a crash after commit | |
| 5 | Documents | US-09, US-10 | No missing or partial file is ever reported as ready | |
| 6 | MCP layer, assistant and evaluations | US-11, US-12 | Tools enforce the acting user's permissions, and deterministic evaluations run without a model | |
| 7 | Minimal frontend | All workflows | Every required workflow and failure state can be completed in the UI | |
| 8 | Delivery | Guide deliverables | A clean clone runs from the README, and the demo scenarios reproduce | |

### 2.3 Design decisions still to make

| Decision | Iteration |
|---|---|
| Itinerary storage, and how its edits use `version` | 3 |
| Provider client, timeouts and retries | 3 |
| Schedule conflict rules | 3 |
| Background work mechanism, without a message broker | 4 |
| Document format, file storage, and file-to-record consistency | 5 |
| MCP SDK and transport, LLM provider, deterministic mode (1.4, item 2) | 6 |
| Deployment proposal (1.4, item 3) | 8 |

### 2.4 Next: Iteration 3, provider client, search, itinerary and budget

A plan, not started. It begins only after Iteration 2 is merged with green CI.

**Scope**

- `app/providers/`: the HTTP client, bound to `contracts/openAPI.json`, parsing strictly (A45).
- Search (US-03): one search per destination (A37). Complete, partial, empty and failed results stay distinct, a failed supplier does not lose the others, and results show their freshness.
- Itinerary (US-04): editors add, remove and arrange selected options. An offer is stored as a JSONB snapshot, as `CLAUDE.md` decided. The application reports missing information, expired prices and schedule conflicts, and explains any simplified rule.
- Budget (US-05): itemized costs, a total and a comparison, in USD. `ROUND_HALF_UP` lands in `app/money.py`.
- Concurrent edits (US-06): the `version` check extends to the itinerary, and a stale edit gets a usable 409.

**Out of scope:** bookings, documents, MCP, the frontend, and any change to the mock or the contract.

**Decisions to settle first**

Each still needs a recommendation and a test.

| # | Decision | Question |
|---|---|---|
| 1 | Provider client | Which HTTP client, and which timeouts? A43 needs the client timeout to exceed the whole search. |
| 2 | Itinerary storage | How items are stored, and whether an item edit bumps the trip's `version` or has its own. |
| 3 | Editor role | `app/api/deps.py` needs a third permission, because the PRD gives editors the itinerary. |
| 4 | Schedule conflicts | Which overlaps count, given that no field gives a destination's time zone (A31). |
| 5 | Estimates against confirmed prices | How a cost keeps that distinction before bookings exist, so Iteration 4 needs no migration of meaning. |

**Rules from `CONTRACT_NOTES.md` that reach this iteration:** A25, A28, A29, A31, A37, A43, A44 and A45.

**The team in this iteration**

The way of working from Iteration 2 held up (2.8), so it stays:

- One brief, written by the Director, is the single spec for both authors.
- Claude Sonnet builds the application. Codex writes the tests from the brief without reading `app/`.
- Each reviews the other's artifact, and each author fixes its own findings.
- The Director reproduces every finding before routing it, and runs everything Codex wrote, because its sandbox denies Python and Docker.
- A claim that something can fail needs a control run that fails for the stated reason. An exit code alone proves nothing.

**Tests that must exist, and must be able to fail**

They run over real HTTP, with the mock and Postgres as real processes.

1. Every client response is parsed against the contract, and a schema-invalid `2xx` is refused.
2. Complete, partial, empty and failed searches are told apart, and one failed supplier keeps the other results.
3. Search results are never presented as confirmed prices.
4. Itinerary changes survive a restart, and only editors and organizers make them.
5. Expired prices, missing information and schedule conflicts are reported.
6. Totals are reproducible, and rounding happens only in `app/money.py`.
7. Concurrent itinerary edits with the same `version` produce exactly one success, and the loser gets the current state.

**Done when:** all of the above pass, `tests/mutation_check.py` covers the new rules with every mutation killed, `docker compose up` works from clean, CI is green, and this file is updated.

### 2.5 Starter prompt for the next iteration

Before pasting it, make sure this plan is on `main` and that the main checkout has pulled it, because a new session starts from that code. Start the session on Opus at high effort, because it acts as the Director.

```text
Read CLAUDE.md, ARCHITECTURE.md, docs/STATUS.md and docs/prd.md completely before
writing anything. From docs/CONTRACT_NOTES.md, read Sections 1.0 to 1.7, and the
entries A25, A28, A29, A31, A37, A43, A44 and A45.

We are starting Iteration 3 as defined in docs/STATUS.md, Section 2.4. You are the
Director.

First, confirm that Iteration 2 is on main with green CI. Then list the decisions in
Section 2.4 with your recommendation, testing what has not been tested yet, and wait
for my answers. Then implement only that scope, with the roles in CLAUDE.md, and have
Codex and Claude Sonnet review each other's artifacts. Every test must be able to fail.

Do not start Iteration 4. When done, update docs/STATUS.md and show the test results.
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

### 2.8 Iteration 2 record

Built on branch `claude/iteration-2-director-089f39`. Not pushed yet.

**What exists:** `app/` (`api`, `domain`, `services`, `db`, `money.py`, `config.py`, its Dockerfile and requirements), Postgres and the `migrate` and `app` services in `docker-compose.yml`, and `tests/test_app_*.py` with `tests/app_support.py`. `tests/compose_smoke.py` and `tests/mutation_check.py` now cover the application.

**Decisions taken by the owner**

The reasoning is in `ARCHITECTURE.md`.

| Decision | Outcome |
|---|---|
| Migrations | Alembic, forward-only, through a one-shot `migrate` service, with `compare_server_default=True` and direct SQL tests for what `alembic check` misses |
| Test identities | `token:user` pairs in `APP_TOKENS`, a dictionary lookup, and no `users` table |
| Refusals and roles | 401, 404, 403, then 422. Organizer-only trip edits, an immutable organizer row, and a partial unique index |
| Money and dates at the API | `str` with a pattern tighter than the contract's, and dates as `str` plus a pattern plus `date.fromisoformat` |
| Malformed JSON before 401 or 404 | Accepted as a limit |

**Required tests**

| # | Required test | Where |
|---|---|---|
| 1 | A trip survives a hard kill and a replaced Postgres container | `tests/compose_smoke.py`, and `test_budget_round_trips_after_hard_restarts_and_patch` for the application process |
| 2 | Invalid input returns an actionable error and saves nothing | `tests/test_app_validation.py` |
| 3 | A budget round-trips exactly | `test_budget_round_trips_after_hard_restarts_and_patch` |
| 4 | The `/openapi.json` walk refuses a missing token and a nonparticipant | `test_openapi_walk_enforces_auth_and_nonparticipant_hiding`, `test_no_runtime_route_can_hide_from_openapi` |
| 5 | A role matrix over every write route | `test_every_organizer_write_refuses_viewers_and_editors_before_validation` |
| 6 | A nonparticipant, a missing trip and a malformed id look the same | `test_nonparticipant_missing_and_malformed_trip_are_indistinguishable` |
| 7 | Creating a trip is atomic | `test_create_trip_rolls_back_every_row_when_organizer_insert_fails` |
| 8 | A stale `version` gets a 409, and concurrent edits have one winner | `tests/test_app_transactions.py`, 24 requests with forced overlap |
| 9 | Concurrently adding a participant creates one row | `test_24_concurrent_adds_create_exactly_one_participant` |
| 10 | Migrations apply on an empty database with no drift | `tests/test_app_database.py` |
| 11 | Every error body has the error shape | `tests/test_app_errors.py`, and the client hook in `tests/app_support.py` |

**Evidence**

| Check | Result |
|---|---|
| `python -m pytest tests` | 316 passed, in about 70 seconds: 234 for the mock and 82 for the application |
| Responses declared in the contract and exercised | 28 of 28 |
| `python tests/mutation_check.py` | 107 of 107 mutations killed, in 36 minutes: 72 for the mock and 35 for the application. `python tests/mutation_check.py app` runs only the application's. |
| Image built without cache, then `python tests/compose_smoke.py` | Passes |
| `docker compose up` from clean, 15 full starts | 0 failed. It was 1 of 15 before the `db` healthcheck moved to TCP |
| Control: the smoke with the database volume removed | Fails with `trip read answered 404, so the trip was lost`, which proves it can fail |
| Control: a new route added to the organizer router | The `/openapi.json` walk passes, so the route inherits the router's protection. Mutation A05 removes that protection and dies |
| Cross-review | Every artifact was reviewed by the assistant that did not write it (`AI-USAGE.md`) |
| `contracts/openAPI.json` | SHA-256 unchanged: `9bf9b93e…` |
| GitHub Actions | ⚠ Not run: nothing has been pushed. `.github/workflows/ci.yml` needs no change, because `pytest tests` and the smoke already reach the application. The tests have only run on Windows |

**Limits accepted:** "Limits I accept" in `ARCHITECTURE.md`.
