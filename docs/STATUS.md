# Project status and continuity plan

As of `main` at `5e3b1c0`, with Iterations 4 and 5 merged through pull requests #6 and #8, and CI green on both jobs. CI failed once on `main` at `7a686d5`, on a race in an Iteration 2 test (2.7).

**Where we stand:** Iterations 1 to 5 are merged. Iteration 6 (MCP layer, assistant and evaluations) is built, verified and committed on its branch, with no pull request yet (2.14). Iteration 7 is in pull request #10. The application has trips, participants and permissions, search, an itinerary with a budget, bookings with background reconciliation, and trip documents. What is left is Iteration 6 (MCP and assistant), Iteration 7 (minimal frontend) and Iteration 8 (delivery), on the revised plan to the 09:00 deadline in 2.2. The next sessions start from the prompts in 2.5.

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
| Team roles, Codex sandbox and contract barrier | Done. Subagent models are confirmed by self-report only, their effort is not visible, and the owner accepted them | `CLAUDE.md`, `AI-USAGE.md` |
| Ponytail (YAGNI) for Claude and Codex | Done | `CLAUDE.md`, `AGENTS.md`, `.claude/settings.json` |
| Architecture decisions | Recorded. Modular monolith confirmed by the owner. | `CLAUDE.md`, `ARCHITECTURE.md` |
| Application data model and API | Done for trips, participants, search, the itinerary, the budget, bookings and documents | `ARCHITECTURE.md`, and Sections 2.8, 2.9, 2.11 and 2.12 of this file |
| Provider client | Done: contract-bound parsing, one deadline per request, bounded retries, bookings and by-reference reads | `ARCHITECTURE.md`, Provider client |
| Bookings and reconciliation | Done: attempts committed before the POST, the 2.8 outcome table, monotonic states, an in-process poller | `ARCHITECTURE.md`, Bookings |
| MCP tools and assistant | Done: `mcp` 2.3.0 at `/mcp`, five read-only tools behind the API's token and `membership()`, a deterministic assistant and a model mode, evaluations E1 to E7 (2.14) | `ARCHITECTURE.md`, MCP layer and assistant |
| Document generation | Done: HTML in `bytea`, captured in one snapshot, a leased and fenced worker, re-hashed on download (2.12) | `ARCHITECTURE.md`, Documents |
| Deployment | Idea only (1.4, item 3) | |

### 1.2 Development

Nothing below exists yet unless marked.

| Deliverable (candidate guide) | State |
|---|---|
| Supplied contract, unchanged | ✓ Committed. `.gitattributes` keeps its bytes stable, and a test pins its hash |
| Development harness | Partial: `CLAUDE.md`, `AGENTS.md`, GitHub Actions, the team protocol with its hardening and Codex's stand-in `qa-ux`, Ponytail and `.claude/agents/` |
| Provider mock | ✓ Merged |
| Backend | Done: trips, participants, permissions, persistence, provider client, search, itinerary, budget, readiness, bookings and reconciliation, and documents, merged. The MCP layer and the assistant are built in Iteration 6, not merged yet |
| Frontend | Not started |
| Read-only MCP layer and assistant | Built in Iteration 6 (2.14), not merged yet |
| Tests and evaluations | Partial: 234 contract tests for the mock and 392 application tests, 626 in all, run by GitHub Actions on every push. Iteration 6 adds the MCP, assistant and evaluation suites, and `tests/assistant_evals.py` (2.14) |
| Sample data and example configuration | Partial: mock fixtures and `.env.example`, with the `DOCUMENT_*` variables. The `BOOKING_POLL_*` variables are application defaults that compose does not pass |
| `AI-USAGE.md` | Started: harness and log |
| `ARCHITECTURE.md` | Started: decisions, open proposals, guarantees and limits |
| `README.md` | Skeleton: run, test, mutation check, links |
| `DEMO.md` | Not started. `provider-mock/README.md` covers the mock |

User stories implemented: 9 of 12 at API level are merged: US-01 and US-03 to US-10. Iteration 6 builds the other three, not merged yet: US-11, US-12, and the assistant half of US-02 (2.14). The interface is Iteration 7.

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
| 2 | Closed on 2026-11-08: a deterministic assistant by default, like B-Agent's fixed policy, and Claude (`claude-sonnet-5-5`) as an opt-in mode over the same MCP tools (D2, 2.14). | Nothing |
| 3 | Deployment target. The local notes propose Azure Container Apps. Only a proposed approach is required. Parked by the owner. | Iteration 8 |
| 4 | Closed on 2026-11-07: the owner set the deadline at Sunday 2026-11-08, 09:00 (UTC-3). It drives the plan for Iterations 4 to 8. | Scope cuts |
| 5 | Closed on 2026-11-07: the pasted recommendations came from AI, debated and validated with Gemini Flash and researched with Google AI (`AI-USAGE.md`). | Nothing |
| 6 | The owner asked for documentation of each TTL tested, once tests cover them. Today the suite runs one TTL, the 900 second default, with the clock pinned around `validUntil`. The pinnable clock stays test-only. | Nothing yet |
| 7 | The Gemini CLI refuses this account with `IneligibleTierError`. Restoring it is optional, because nothing depends on Gemini. | Nothing |
| 8 | The owner set `working_dir` to `C:/DEV/TravelPlanner`, and a live `clink` through a fresh PAL proves it keeps Codex out of `C:\DEV` and other projects (`AI-USAGE.md`). Each session has its own PAL, cached at start. ⚠ Sessions opened before the change keep `C:\DEV`. `pal_qa` can still write, which `"role_args": ["-s", "read-only"]` would fix. The roles `default`, `planner` and `codereviewer` pass no `-s` either, so they can write too (read on 2026-11-07). Only the owner changes that file. | Other projects, while old sessions stay open |
| 9 | The complete Codex review of `CONTRACT_NOTES.md` found no contradictions and three wording notes: line 106, line 182 and A44 (`AI-USAGE.md`). Rewording them is the owner's call, because those notes are accepted. | Nothing |
| 10 | Closed on 2026-11-07: Iteration 2 is merged, with CI green on the branch and on `main` (2.8). | Nothing |
| 11 | Closed on 2026-11-07: `clink` in a fresh PAL starts in `C:\DEV\TravelPlanner` and is denied outside it. It still writes in the main checkout and its `contracts/`, so `CLAUDE.md` keeps it banned here (`AI-USAGE.md`). | Nothing |
| 12 | Closed on 2026-11-08: four pairs of orphan `uvicorn` processes from this worktree's early test runs had a dead parent. Sonnet's `Stop-Process` was denied by the permission classifier, so it was left to the owner. At 02:05 none of this worktree's processes remained. | Nothing |
| 13 | `tests/test_offers.py::test_suppliers_are_queried_concurrently_within_one_deadline` flaked once under load: 0.391 s against a 0.4 s floor, Windows timer resolution. The mock did not change. 2.6 already lists this test as a wall-time risk. | Nothing yet |
| 14 | CI failed once on `main` at `7a686d5` (2.7). A two-line test fix, validated by the owner, is in the close-out pull request of Iteration 4. | Nothing: `main` is green at `5e3b1c0` |

### 1.5 Housekeeping

- **This file is versioned since Iteration 1,** so worktrees can read it. It used to be untracked in the main checkout.
- **Auto-fix has no app-wide default,** so the rule in `CLAUDE.md` is what turns it on. It covers pull requests opened from a Claude Code session, not ones opened by hand.
- **`docs/observaciones.md` is local only.** It is excluded through `.git/info/exclude`, so it has no backup and does not exist on other clones or in worktrees.
- **Cases where AI was wrong live in `AI-USAGE.md`,** which `CLAUDE.md` requires for every new one.

---

## 2. Continuity plan

### 2.1 Working rules for every iteration

1. Start each iteration in a clean context, from its prompt in 2.5. Two iterations may run in parallel sessions on one machine: they agree on migration numbers, mutation ids and the order of heavy jobs by session message, as Iterations 4 and 5 did.
2. Read the contract and the notes before touching provider-facing code.
3. Resolve the iteration's open design decisions first, in small batches, each with a recommendation.
4. Test a recommendation before accepting it, whether it comes from a person or from an AI.
5. Close by updating this file, then commit only when the owner asks.
6. Follow "CI and pull requests" in `CLAUDE.md`: every pull request is born with Auto-fix on, every CI failure is alerted and logged in 2.7, and every fix needs the owner's review and validation.
7. Follow "Multi-model assistant protocol (PAL)" in `CLAUDE.md`: Codex runs from the CLI with `-s` and `< /dev/null`, never through `clink`, whoever wrote a change does not review it, and mistakes go in `AI-USAGE.md`.
8. Apply Ponytail as `CLAUDE.md` limits it. Before closing an iteration, review `ponytail:` comments with `/ponytail-debt`.
9. Every worktree has its own `.venv`, which git ignores. Build it before the first test with `python -m venv .venv` and `.venv/Scripts/python -m pip install -r tests/requirements.txt`, rerun the install when a requirements file changes, and run every check with it.
10. Time one sample of a long check before the full run, and check its anchors first. The first app mutation run would have taken about 2.5 hours, and two of its entries were stale. In Iteration 4 a sample took 370 s per entry, until each entry named its killing test in `APP_KILLERS`, which the runner tries first: 57 entries then took 16 minutes.
11. Run one heavy local job at a time: the suite, the mutation check or the compose smoke. Side by side, the suite slowed from 216 s to 354 s and a timing test flaked.
12. Freeze `app/` before mutation anchors are written. Mutations written while an author was still changing the code went stale.
13. After stopping a background job, list processes by command line and stop the whole tree. `TaskStop` left the mutation run, its `pytest`, a mock and a Postgres container alive, in Iteration 2 and again in Iteration 3.

### 2.2 Iterations

| # | Goal | Stories | Done when | State |
|---|---|---|---|---|
| 1 | Repository base and provider mock | Foundation for US-03, US-07, US-08 | `docker compose up` runs the mock, and contract tests pass against it | ✓ Merged (2.6) |
| 2 | Application core: persistence, test identities, trips, permissions | US-01, US-02 | Trips survive a restart, and nonparticipants are refused on every route | ✓ Merged (2.8) |
| 3 | Provider client, search, itinerary, budget, concurrent edits | US-03, US-04, US-05, US-06 | Partial, empty and failed searches differ, and a stale edit returns a usable 409 | ✓ Merged (2.9) |
| 4 | Booking and reconciliation | US-07, US-08 | Every row of `CONTRACT_NOTES.md` 2.8 has a test, including a crash after commit | ✓ Merged (2.11) |
| 5 | Documents | US-09, US-10 | No missing or partial file is ever reported as ready | ✓ Merged (2.12) |
| 6 | MCP layer, assistant and evaluations | US-11, US-12 | Tools enforce the acting user's permissions, and deterministic evaluations run without a model | Built and verified (2.14), committed on its branch |
| 7 | Minimal frontend | All workflows | Every required workflow and failure state can be completed in the UI | |
| 8 | Delivery | Guide deliverables | A clean clone runs from the README, and the demo scenarios reproduce | |

**Plan to the deadline,** approved by the owner on 2026-11-07 at 20:40 (UTC-3). The deadline is Sunday 2026-11-08, 09:00.

| Block | Iteration |
|---|---|
| 20:30 to 22:30 | Close 3: mutation check, `docker compose up` from clean, documents, and a commit when the owner asks |
| 22:30 to 02:30 | 4, bookings |
| 02:30 to 05:00 | 5 and 6 in parallel, each in its own session |
| 05:00 to 07:30 | 7, minimal frontend |
| 07:30 to 08:45 | 8, delivery |

**Revised plan, proposed by the Director at 03:40 (UTC-3) and approved by the owner at 03:50.** Iteration 4 ran past its block, and Iteration 6 has not started, so 6 and 7 run in parallel. The frontend needs only the merged API. The assistant panel joins it after 6 merges.

| Block | Iteration |
|---|---|
| 03:45 to 06:15 | 6, MCP layer, assistant and evaluations, in its own session |
| 03:45 to 06:45 | 7, minimal frontend without the assistant panel, in its own session |
| 06:45 to 07:45 | Merge 6, then 7, then the assistant panel. One heavy job at a time |
| 07:45 to 08:45 | 8, delivery |

Coordination between 6 and 7, by session message, as 4 and 5 did: 6 owns any migration `0005` and mutation ids A184 onward. 7 adds no migration, and its frontend checks live under `web/`. Each opens its own pull request into `main`, so no retarget step is needed (2.12, As it ran). The heavy jobs on this machine take turns.

Process for Iterations 4 to 8: one cross-review round per artifact, and the mutation check covers only each iteration's new rules. Codex and Sonnet still review each other on every artifact.

### 2.3 Design decisions still to make

| Decision | Iteration |
|---|---|
| Document format, file storage, and file-to-record consistency | 5, decided (2.12) |
| MCP SDK and transport, LLM provider, deterministic mode (1.4, item 2) | 6, decided (2.14) |
| Deployment proposal (1.4, item 3) | 8 |

### 2.4 Iteration 3 plan, as it was run

Built. The record, with its evidence, is 2.9. Iteration 2, its precondition, was merged with green CI.

**Decisions taken by the owner on 2026-11-07,** all as recommended, each measured first. The reasoning and the measures are in `ARCHITECTURE.md`.

| # | Decision | Outcome |
|---|---|---|
| 1 | Provider client | `httpx.AsyncClient`, async only where the provider is called, no database transaction open while it answers. 10 s timeout plus a total bound. Search never retries, and `getOffer` makes at most two attempts |
| 2 | Itinerary storage | `itinerary_items` with the provider's offer as JSONB, positions deferrable-unique, and every write bumping `trips.version`. The server fetches the offer, so the client sends only its id |
| 3 | Editor role | `trip_editor`: the organizer and editors write the itinerary |
| 4 | Schedule conflicts | Instants, half-open. Same category, or transport with activity. Accommodation only with accommodation |
| 5 | Estimates and confirmed prices | `priceKind` derived, not stored. `format_amount` rounds half up |
| 6 | Party size, added | `trips.travelers`, 1 to 20 |
| 7 | Strict parsing, added | `jsonschema` against the contract at runtime, plus amount, instant and string checks |

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

### 2.5 Starter prompts for the next sessions

Iterations 6 and 7 start now, in parallel, each in its own session, on the revised plan in 2.2. `main` at `5e3b1c0` holds Iterations 1 to 5 with green CI. Before pasting a prompt, make sure the main checkout has pulled `main`, because a new session starts from that code. Start each session on Opus at high effort, because it acts as the Director. Paste the first one, then the second: they coordinate by session message.

**Iteration 6, MCP layer, assistant and evaluations**

```text
Read CLAUDE.md, ARCHITECTURE.md, docs/STATUS.md and docs/prd.md completely before
writing anything, and the user stories US-02, US-11 and US-12 twice.

We are starting Iteration 6 (MCP layer, assistant and evaluations) as defined in
docs/STATUS.md, Section 2.2. You are the Director. Iteration 7 (the frontend) runs in
parallel in another session.

First, build .venv in your worktree as rule 9 of Section 2.1 says, run the tests with
it, and confirm that main is at 5e3b1c0 or later with green CI. Then list, in one batch,
the decisions with your recommendation: the MCP SDK and transport, the LLM provider and
a deterministic mode so evaluations run without a model (1.4, item 2), how a tool takes
the acting user's identity and reuses the API's permission checks (US-12: a trip id
alone grants nothing), which read-only tools exist (trip, itinerary, budget, bookings,
documents, option search), and how the evaluations are scored. Start from the proposal on
read-only views in ARCHITECTURE.md, probe what has not been probed, and wait for my
answers. Then implement only that scope, with the roles in CLAUDE.md, and have Codex and
Claude Sonnet review each other's artifacts, one round each. If Codex stops for a usage
limit, qa-ux takes its place at once. Every test must be able to fail.

Coordinate with the Iteration 7 session by message: you own any migration 0005 and the
mutation ids A184 onward, you open your own pull request into main, and heavy jobs on
this machine take turns (rule 11). Tell it the assistant's HTTP route and response
shape as soon as they are decided, because its UI adds the panel after your merge.

Follow the revised plan to the deadline in Section 2.2 (Sunday 09:00, UTC-3) and
working rules 10 to 13 of Section 2.1: run the mutation check only on the new rules,
with a killing test per entry in APP_KILLERS. When done, update docs/STATUS.md and show
the test results. Do not commit until I ask.
```

**Iteration 7, minimal frontend**

```text
Read CLAUDE.md, ARCHITECTURE.md, docs/STATUS.md and docs/prd.md completely before
writing anything, and every user story's acceptance criteria twice.

We are starting Iteration 7 (minimal frontend) as defined in docs/STATUS.md, Section
2.2. You are the Director. Iteration 6 (MCP and assistant) runs in parallel in another
session, so build everything except the assistant panel, which joins after 6 merges.

First, build .venv in your worktree as rule 9 of Section 2.1 says, run the tests with
it, and confirm that main is at 5e3b1c0 or later with green CI. Then list, in one batch,
the decisions with your recommendation: the stack inside web/ (CLAUDE.md fixes React and
TypeScript, minimal, no visual polish), how the UI reaches the API and picks a test
identity, how it shows every failure state the PRD requires (partial, empty and failed
searches, a stale-edit 409 with the current state, unknown, pending, failed and
rejected bookings with their next step, document states), how status is refreshed by
polling the state endpoints, how docker compose serves it, and how it is tested so a
test can fail. Probe what has not been probed, and wait for my answers. Then implement
only that scope, with the roles in CLAUDE.md, and have Codex and Claude Sonnet review
each other's artifacts, one round each; qa-ux reviews the UI and UX. If Codex stops for
a usage limit, qa-ux takes its place at once. Every test must be able to fail.

Coordinate with the Iteration 6 session by message: you add no migration and no
mutation id, your checks live under web/, you open your own pull request into main, and
heavy jobs on this machine take turns (rule 11). Never change app/ behaviour to suit
the UI without telling the owner.

Follow the revised plan to the deadline in Section 2.2 (Sunday 09:00, UTC-3). When done,
update docs/STATUS.md and show the test results. Do not commit until I ask.
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
- Found in Iteration 3: an offer whose instants use a lowercase `t` or `z`, which the contract allows, breaks the mock. Its search answers 503 and `getOffer` fails. Only the control interface can store such an offer, so no workflow reaches it. The application's handling of those instants is tested through a stub instead.

### 2.7 CI failure log

Rule 6 of 2.1 applies. Newest first. A fix stays a proposal until the last column names the owner's validation.

| Date | Commit | Job | Cause | Fix | Validated by owner |
|---|---|---|---|---|---|
| 2026-11-08 | `7a686d5`, the merge of #6 into `main` | `contract-tests` | `tests/test_app_transactions.py::test_create_trip_rolls_back_every_row_when_organizer_insert_fails`, line 51: `RemoteProtocolError: Server disconnected without sending a response`. After an unhandled exception whose 500 was already sent, uvicorn closes the connection (`uvicorn/protocols/http/h11_impl.py:417-420`), and the test's pooled keep-alive connection is reused for the next POST, racing that close. A timing race: the same tree passed on #6 and #8 and locally. 1 failed, 610 passed | Proposed in the close-out pull request of Iteration 4: the 500-provoking request sends `Connection: close` | Yes, on 2026-11-08 at 03:50 (UTC-3), in the Iteration 4 session |

### 2.8 Iteration 2 record

Merged through pull request #3, as `df7ae61`.

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
| GitHub Actions | Both jobs passed on `835c447` and on `main` at `df7ae61`. On Linux, `pytest tests` gave 316 passed with none skipped, and the compose smoke passed. `.github/workflows/ci.yml` needed no change |

**Limits accepted:** "Limits I accept" in `ARCHITECTURE.md`.

### 2.9 Iteration 3 record

Built on branch `claude/iteration-3-setup-1e733b`, and sent to `main` through a pull request at the owner's request.

**What exists:**

- `app/`: `providers/client.py`, `services/search.py`, `services/itinerary.py`, `domain/itinerary.py`, `api/search.py`, `api/itinerary.py`, migration `0002`, `GET /ready`, and the shared snapshot read in `db/session.py`.
- `tests/`:
  - New suites: `test_app_provider.py`, `test_app_itinerary.py`, `test_app_itinerary_permissions.py`, `test_app_concurrency.py`, `test_app_database_constraints.py` and `test_app_search_itinerary_validation.py`.
  - Support: a provider stub in `tests/app_support.py`, and a mutation runner that runs the mapped tests first.
- Harness and docs: `.claude/agents/qa-ux.md`, hardening for every agent, and `docs/ROADMAP-REVIEW.md`.

**Decisions taken by the owner:** the 7 in 2.4. In the evening the owner also took O5, O7, O8 and O11 into this iteration, adopted O2 and O3, and set the deadline (`docs/ROADMAP-REVIEW.md`, Section 5).

**Required tests**

| # | Required test | Where |
|---|---|---|
| 1 | Every client response is parsed against the contract, and a schema-invalid `2xx` is refused | `tests/test_app_provider.py` |
| 2 | Complete, partial, empty and failed searches are told apart, and one failed supplier keeps the other results | `tests/test_app_provider.py` |
| 3 | Search results are never presented as confirmed prices | `tests/test_app_provider.py`, `tests/test_app_itinerary.py` |
| 4 | Itinerary changes survive a restart, and only editors and organizers make them | `tests/test_app_itinerary.py`, `tests/test_app_itinerary_permissions.py`, `tests/compose_smoke.py` |
| 5 | Expired prices, missing information and schedule conflicts are reported | `tests/test_app_itinerary.py` |
| 6 | Totals are reproducible, and rounding happens only in `app/money.py` | `tests/test_app_itinerary.py` |
| 7 | Concurrent itinerary edits with the same `version` produce exactly one success, and the loser gets the current state | `tests/test_app_concurrency.py`, `tests/test_app_transactions.py` |

**Evidence**

| Check | Result |
|---|---|
| `python -m pytest tests` | 482 passed, in about 5 minutes and 20 seconds: 234 for the mock and 248 for the application |
| Responses declared in the contract and exercised | 28 of 28 |
| `python tests/mutation_check.py app` | 91 of 91 killed, with no stale entry and no false kill, in 72 minutes (4331 s). That is over the 60 minutes of O9, so Iterations 4 to 8 mutate only their new rules. The runner tries the mapped test files first, and confirms a survivor against the whole app suite. The `Retry-After` mutation, A94, was added last and was killed alone, which makes 92 of 92. The mock's 72 were not rerun, because the mock did not change |
| Image built without cache, then `python tests/compose_smoke.py` | Passes. It creates a trip, searches it through the compose network (status `complete`), adds an offer, replaces the app and reads the item back, then replaces the volume-backed containers and reads the trip back |
| Control: the smoke with `PROVIDER_TOKEN=wrong` | Fails with `packaged app did not reach the mock`, which proves the search check can fail |
| Defects found by review, not by the suite | Six in `app/`, all reproduced on real processes, then fixed and tested: a NUL from the provider reaching jsonb, the reorder and delete races, the 41-digit query version, a NUL in `providerId`, and the `PATCH /trips` race (`AI-USAGE.md`) |
| A 200-search burst against a provider holding each answer 3 s | All 200, in 7.8 s, with no pool stall |
| Cross-review | Every artifact was reviewed by the assistant that did not write it (`AI-USAGE.md`) |
| `contracts/openAPI.json` | SHA-256 unchanged: `9bf9b93e…` |
| GitHub Actions | Runs on the pull request. The next session confirms it is green on `main` before Iteration 4 |

**Limits accepted:** "Limits I accept" in `ARCHITECTURE.md`, and the mock's lowercase instants in 2.6.

### 2.10 Iteration 4 plan, as it was run

Built. The record, with its evidence, is 2.11. The scope is row 4 of 2.2, US-07 and US-08.

**Decisions taken by the owner on 2026-11-07 at 23:20 (UTC-3),** all five as recommended below, with two changes from the drafts: the booking route takes no trip `version` and never bumps it, and the poller needs no lease or fence for bookings, because reads are idempotent and states only move forward. The two drafts without a probe were probed first: 24 manual races of delete against book on a `RESTRICT` foreign key gave one winner each and no orphan, and its control with `ON DELETE CASCADE` failed. A request id in a `ContextVar` reached the thread pool, async routes and sync routes without crossing requests.

**Decisions to settle first**

| # | Decision | Question |
|---|---|---|
| 1 | Background mechanism | How pending and `unknown` attempts are polled without a broker (2.3, A15, A20) |
| 2 | One item per offer | Whether an itinerary may hold the same offer twice (O6, A12) |
| 3 | Attempt storage | The table behind A12's one active attempt per item, and what it stores (2.7) |
| 4 | Diagnostics | One log line per provider call that carries both request ids, and a test that reads it (O4) |
| 5 | README | The skeleton that starts at this iteration's close (O10) |

**The Director's draft recommendations,** written during Iteration 3, kept as the drafts the owner decided from. Row 4 as built differs: the body is `{expectedRevision, expectedPriceUsd}`, with no `version`.

| # | Recommendation | Probe |
|---|---|---|
| 1 | One in-process loop, started by the app's lifespan. It reads open attempts from Postgres, polls by reference with backoff (A15, A20), only reads (A14), and resumes from the database after a restart. Iteration 5 reuses it: a job is claimed with `FOR UPDATE SKIP LOCKED`, committed `in_progress` with a lease, and its final write is fenced. Documents run before booking polls | 4 workers draining 200 jobs claimed each once |
| 2 | `booking_attempts`, committed as `unknown` before the POST, holding the key, the reference (A10), the exact body and the provider's booking. A partial unique index on the item, `WHERE state NOT IN ('rejected', 'failed')`, allows one open attempt and none after `confirmed` (A12) | Of 24 simultaneous inserts, one entered. A second open attempt and an attempt after `confirmed` were refused. One after `rejected` was allowed |
| 3 | A unique index on `(trip_id, (offer->>'id'))`, and a 409 on add (O6) | A repeated offer was refused. `alembic` does see that expression index missing from a model |
| 4 | `POST .../items/{itemId}/booking` with `{version, expectedRevision, expectedPrice}` in the draft (built as `{expectedRevision, expectedPriceUsd}`), as reviewed after a refresh (A25, A27). A mismatch is a 409 with no POST. Retry of an `unknown` attempt is its own route, and resends the stored key and body (A13, A14) | Nothing to probe: contract rules |
| 5 | A request id from middleware in every log line and error body, one log line per provider call with both ids, and `AppProcess` logging to a file so a test can read it | Probed at decision time: see the paragraph above |

Also decided: an item with an open or confirmed attempt cannot be deleted, with a 409 (US-08). Built.

**Iteration 5, drafted ahead** by `director-proyecto`, for the owner to decide after Iteration 4:

- **Format:** HTML from the standard library.
- **Storage:** `bytea` in the document row.
- **Consistency:** content, hash and `complete` are written in one transaction, a CHECK guards them, and every download re-hashes.
- **Snapshot:** the source is read in one `REPEATABLE READ` snapshot, and stored afterwards outside it. Probed: 11 of 12 concurrent `ON CONFLICT` inserts inside `REPEATABLE READ` failed with a serialization error, so the insert must stay outside the snapshot.
- **Jobs:** states with a lease and a fence. A repeated request returns the same document, through a unique source hash.

**Done when:** every row of `docs/CONTRACT_NOTES.md` 2.8 has a test, including a crash after commit. The mutation check covers the new rules, `docker compose up` works from clean, CI is green, and this file is updated.

### 2.11 Iteration 4 record

Merged into `main` through pull request #6, as `7a686d5`. Built on branch `claude/iteration-4-director-407c30`, committed as `661ee21`.

**What exists:**

- `app/`: `services/booking.py` (book, retry, read, the claim and `reconcile_forever`), `domain/booking.py` (the 2.8 outcome table, monotonic transitions, the attempt view), `db/attempts.py`, `context.py` (the request id), migration `0003` (`booking_attempts`, `uq_one_open_attempt`, `uq_itinerary_offer`), the lifespan task and request-id middleware in `main.py`, `createBooking` and `getBookingByReference` in `providers/client.py`, and the booking state in the itinerary view and budget.
- Routes, under `/trips/{tripId}/itinerary/items/{itemId}`: `POST .../booking` and `POST .../booking/retry` for editors, `GET .../booking` for every participant. None takes or bumps the trip `version`.
- `tests/`: `test_app_bookings.py` (Codex), and `booking_support.py`, `test_app_booking_recovery.py`, `test_app_booking_concurrency.py`, `test_app_booking_views.py`, `test_app_diagnostics.py` and `test_app_migration_0003.py` (`qa-ux`). `AppProcess` now logs to a file a test can read. `mutation_check.py` gained A125 to A181 and `APP_KILLERS`, a killing test per entry, tried first.
- Docs: `docs/iteration-4-brief.md` (the spec), `README.md` (skeleton), and the Bookings, One item per offer and Diagnostics sections of `ARCHITECTURE.md`, with their guarantees and limits.

**Decisions taken by the owner:** the five in 2.10. During the build the Director decided, within that scope: an item with an open or confirmed latest attempt is not `bookable`, and a confirmed item raises no `PRICE_EXPIRED` or `UNAVAILABLE`, because its price is the accepted one (US-05). The loser of a simultaneous insert gets the winner in a 409, `BOOKING_CONFLICT` when the winner already closed. A click that arrives after a closed attempt starts a new attempt at the same terms, which rule 3 of 2.7 allows.

**Required tests,** from the brief

| # | Required test | Where |
|---|---|---|
| 1 | Every row of 2.8, both columns: state, key, reference, POST count, and the ERROR log rows | `test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state` |
| 2 | Crash after commit: POST held, then a hard kill, then a same-key byte-equal retry; and provider accepted, answer lost, reconciled with no second POST. The `unknown` row is read from Postgres while the POST is held | `test_app_booking_recovery.py`, the two crash tests |
| 3 | Polling: pending to confirmed and to failed with no user action, 404 never changes `unknown`, the loop never POSTs, it resumes after a restart, it can be turned off, it follows its intervals and `Retry-After` | `test_app_booking_recovery.py` |
| 4 | Terms: a mismatch or an unavailable snapshot makes no provider call; a quote changed at the provider is `rejected` and the snapshot is untouched | `test_app_bookings.py`, `test_app_booking_views.py` |
| 5 | One open attempt: 24 concurrent books make one POST and one attempt, and 12 staged simultaneous inserts against a winner that closes at once make no second POST | `test_app_booking_concurrency.py` |
| 6 | One item per offer, sequential and concurrent | `test_app_bookings.py`, `test_app_booking_concurrency.py` |
| 7 | Delete refused while open or confirmed, allowed after closed, and a race with a book never leaves both or an orphan | `test_app_booking_concurrency.py` |
| 8 | A confirmed line keeps the accepted price after the provider's price changes, and mixed outcomes stay side by side | `test_app_bookings.py` |
| 9 | Viewers get 403 on book and retry and read the state; nonparticipants get 404; the `/openapi.json` walk covers the new routes | `test_app_bookings.py`, `test_app_permissions.py` |
| 10 | One log line per provider call with both request ids, `X-Request-Id` on the success and error responses tested, and the attempt-to-call lines | `test_app_diagnostics.py` |
| 11 | Migration `0003` with no drift, and each constraint by name in direct SQL | `test_app_migration_0003.py` |

**Evidence**

| Check | Result |
|---|---|
| `python -m pytest tests`, before the iteration | 482 passed, in 302 s, on `main` at `a8b7a33` |
| `python -m pytest tests`, final | 611 passed, in 781 s: 234 for the mock and 377 for the application. The run before Codex's fixes gave 604 passed and 1 failed, the mock timing flake in 1.4, item 13 |
| Responses declared in the contract and exercised | 28 of 28 |
| `python tests/mutation_check.py app A125 … A181` | 57 of 57 killed, no stale entry and no false kill, in 980 s. Sample first, per rule 10: 3 entries took 370 s each before `APP_KILLERS`, and A125 took 15 s after it |
| Control runs | The three tests for Codex's findings failed against the code before the fixes: two 201s, `590 <= 59.9`, and 200 for a `null` body. A181 restores the closed-winner loop and dies. The delete-against-book probe with `ON DELETE CASCADE` failed |
| Image built without cache, then `python tests/compose_smoke.py` | Passes: migration `0003` applies in the `migrate` service, and the app starts with its poller. The smoke does not book: bookings are proven by the suite over real processes |
| Cross-review | Every artifact was reviewed by an assistant that did not write it (`AI-USAGE.md`). Codex reviewed `app/` once its quota returned |
| `contracts/openAPI.json` | `git diff -- contracts/` is empty |
| GitHub Actions | Both jobs green on `661ee21`, in pull request #6 |

**Merge notes for Iteration 5,** agreed with its session by message:

- Iteration 4 owns migration `0003`. Iteration 5 uses `0004`, with `down_revision = "0003"`, and merges after Iteration 4.
- Iteration 5 owns mutation ids A95 to A124, and Iteration 4 A125 to A181. `tests/mutation_check.py` will conflict as a plain append, and `APP_KILLERS` is Iteration 4's.
- Both add a loop to the lifespan in `app/main.py`, and Iteration 5 reads booking states in `app/domain/itinerary.py`, so expect a merge there.
- `tests/test_app_concurrency.py::test_provider_wait_holds_no_database_transaction` scans `pg_stat_activity` for any `idle in transaction`. Iteration 5 starts its app with `DOCUMENT_WORKER_ENABLED=0`. After the merge it must also set `BOOKING_POLL_ENABLED=0`, or the poller's claim can show up there.

**Limits accepted:** "Limits I accept" in `ARCHITECTURE.md`, including the late click, the pool's bound on simultaneous opens, the untested guard that keeps the loop alive, and booking states outside the trip `version`.

### 2.12 Iteration 5 record

Built in parallel with Iteration 4, on branch `claude/wonderful-matsumoto-ba20cf`, from `main` at `a8b7a33`. Committed alone as `3e81a3b`, then merged with Iteration 4 at `661ee21`, after #6's CI was green. The spec is `docs/iteration-5-brief.md`.

**Decisions taken by the owner on 2026-11-07,** all as recommended, each probed on Postgres 17 first.

| # | Decision | Outcome | Probe |
|---|---|---|---|
| 1 | Format | HTML from the standard library, every string through `html.escape`. Downloaded as an attachment with `CSP default-src 'none'` and `nosniff` | None needed |
| 2 | Storage and consistency | `bytea` in the document row. `ck_documents_complete` and `ck_documents_hash` (`content_sha256 = encode(sha256(content),'hex')`). Every download re-hashes; a mismatch is `failed`, 409, and a retry | The database refused `complete` without content, a wrong hash, and an `UPDATE` of content alone. `hashlib` equals Postgres `sha256()` |
| 3 | Snapshot | The source is captured at request time in one `REPEATABLE READ` snapshot, stored as JSONB, with nothing that depends on the clock or the caller. `UNIQUE (trip_id, source_sha256)`, with `ON CONFLICT DO NOTHING` after the snapshot | 24 concurrent inserts in `READ COMMITTED`: 1 row, 0 errors |
| 4 | Jobs | `waiting`, `in_progress`, `complete`, `failed`. A lease with a token, claimed with `SKIP LOCKED`. Every ending write is fenced by the token. 3 attempts. Own loop, `document_worker`, in the lifespan | 4 workers and 200 jobs: 200 distinct claims. An expired lease is reclaimed. The stale write matched 0 rows, and 1 without the token |
| 5 | API and migration | `/trips/{tripId}/documents`: POST 202 or 200, list, one, `/content`, `/retry`. Migration `0004` with `down_revision = "0002"` until the merge | None needed |

**What exists:**

- `app/`: `services/documents.py`, `api/documents.py`, migration `0004`, the `Document` model, a `lifespan` in `main.py`, three `DOCUMENT_*` variables in `config.py`, and `read_items` extracted from `services/itinerary.py`.
- `tests/`: `test_app_documents.py`, with 13 tests. `conftest.py` truncates `documents`, and the session app runs with its worker off. `mutation_check.py` adds A95 to A124. `test_app_concurrency.py`: one app starts with the worker off.
- `ARCHITECTURE.md`: Documents, 6 guarantees, 9 limits.
- Configuration: `docker-compose.yml` and `.env.example` pass `DOCUMENT_WORKER_ENABLED` and `DOCUMENT_LEASE_SECONDS`. The render delay is test-only and is left out of both.

**At the merge with Iteration 4.** A dry run with `git merge-file` against Iteration 4's working tree at 00:55 gave 14 conflict hunks in 7 files: `app/main.py` 3, `tests/mutation_check.py` 3, `app/db/models.py` 2, `app/services/itinerary.py` 2, `tests/conftest.py` 2, `app/config.py` 1 and `ARCHITECTURE.md` 1. `docs/STATUS.md` and `AI-USAGE.md` merged clean, because Iteration 4 had not edited them yet. Git flags none of the following, so each is a step of the merge commit:

1. **Migration heads.** Set `down_revision` in `0004` to `"0003"`. Probed: without it `alembic heads` lists `0003` and `0004`, and `upgrade head` fails.
2. **Booking states.** Done by deleting the `booking_states` stub. `read_items` now reads Iteration 4's `attempts.latest(session, trip_id=...)` inside the caller's snapshot, so the itinerary view and the document capture share one read.
3. **Price kind and budget.** Iteration 4 changed `price_kind(offer)` to `price_kind(item)`, and its `budget()` reads `item["booking"]["booking"]["offer"]` for a confirmed item.
   - As merged untouched, `capture_source` would label every item `estimate`, and a trip with a confirmed booking would raise `KeyError`, a 500.
   - The fix: both run on the items from `read_items`, which carry the full attempt view, and the source stores only `{state, clientReference}`. The attempt view's `lastCheckedAt` changes on every poll, so storing it would break the source hash.
4. **Item price.** The document must show a confirmed item at the accepted price, the budget line, not the snapshot.
5. **Lifespan.** Start the booking loop beside `document_worker`.
6. **Tests.**
   - `conftest.py` truncates both new tables.
   - `test_provider_wait_holds_no_database_transaction` also passes `BOOKING_POLL_ENABLED=0`.
   - The mutation numbers stay apart: A95 to A124 here, A125 onward in Iteration 4.
7. **New tests for the merge.**
   - A document of a trip with a confirmed booking shows `Booking: confirmed`, the price kind `confirmed` and the accepted price.
   - Two requests on either side of a poll return the same document.
8. **Docs.**
   - `README.md`, created by Iteration 4, gains the document routes and variables.
   - Iteration 4's record is also numbered 2.11 and merges first, so this record is 2.12.
   - Iteration 4's `tests/mutation_check.py` runs one known killer first, through `APP_KILLERS`. A95 to A124 work without it. Their killers from this run go there, to make the run faster.

**Delivery plan: pull requests in cascade.** Validated on 2026-11-08 against the repository's settings and the dry run above.

| Step | Action | Check |
|---|---|---|
| 1 | Iteration 4 commits, pushes and opens PR A, `claude/iteration-4-director-407c30` into `main`, with Auto-fix on | CI on its push |
| 2 | Here: commit Iteration 5 alone, then `git merge` the Iteration 4 branch, with steps 1 to 8 above in the merge commit | Full suite, the mutations of the rules the merge touches, compose smoke |
| 3 | Push, and open PR B into the Iteration 4 branch, with Auto-fix on. Its diff is Iteration 5 plus the merge resolution only | CI runs the combined code on the push (`on: push`) |
| 4 | Merge PR A with a **merge commit**, as every earlier pull request was. A squash would rewrite the base of PR B | |
| 5 | `gh pr edit <B> --base main`, before deleting the Iteration 4 branch. `deleteBranchOnMerge` is off, so GitHub does not retarget it | PR B's diff stays Iteration 5 only |
| 6 | Merge PR B | CI on `main` |

- **Why it is worth it:** the merge, its fixes and its tests start as soon as PR A is pushed, not after it is merged. CI tests the combined code before anything reaches `main`, and that is where items 1 and 3 above break.
- **What it costs:** every fix commit on PR A needs a new merge into PR B and another CI run.
- **The alternative:** merge PR A first, then `sync_with_base_branch` and PR B into `main`. It is simpler and loses that overlap.

**As it ran:**

- PR A is #6 and PR B is #7.
- #6 was merged into `main` as `7a686d5`. Then #7 was merged into the Iteration 4 branch as `20bff82`, without step 5, so `main` did not get Iteration 5.
- #8 brings the same head, `a3d1af7`, into `main`. Its diff is Iteration 5 and the merge only, it merges clean, and CI is green on that commit. Auto-fix is on.
- Lesson for the next cascade: retarget PR B as soon as PR A merges, or open PR B against `main` from the start, once PR A is merged.

**Required tests**

| # | Required test | Where, in `tests/test_app_documents.py` |
|---|---|---|
| 1 | Waiting, in progress, complete, and recovery after a restart | `test_request_waits_without_a_worker_...` |
| 2 | Itinerary, costs and booking states, escaped | `test_content_has_every_required_text_...` |
| 3 | A document is a point in time | `test_a_document_stays_the_trip_as_captured_...`, `test_the_source_is_captured_when_requested_...`, `test_the_source_pairs_a_version_with_its_own_items_...` |
| 4 | Repeated requests give one document | `test_repeated_requests_return_the_same_document_...` |
| 5 | An interruption never reports a missing file as ready | `test_a_kill_mid_generation_...`, `test_a_claim_that_makes_the_fourth_attempt_...` |
| 6 | A stale worker cannot overwrite | `test_a_stolen_lease_fences_...` |
| 7 | Failure and retry | `test_an_unrenderable_source_fails_...` |
| 8 | A stored file against its record | `test_a_corrupted_file_is_never_sent_...` |
| 9 | Permissions | `test_viewer_reads_but_cannot_write_...` |
| 10 | Constraints | `test_the_database_enforces_the_documents_invariants_by_name` |

**Evidence**

| Check | Result |
|---|---|
| Baseline, `python -m pytest tests` before any change | 482 passed in 324 s |
| `python -m pytest tests/test_app_documents.py` | 13 passed in 97 s. That run overlapped an Iteration 4 job |
| `alembic upgrade head`, then `alembic check` | No drift (Sonnet's run) |
| Mutation A95, timed before the full run (rule 10) | Killed in 63 s by the fence test |
| `python tests/mutation_check.py app A95 … A124` | 30 of 30 killed in 1600 s, with no stale entry and no false kill. Each was killed by the test its rule names. Unmutated, and recorded as limits: the hash conjunct of the corrupt `UPDATE`, `SKIP LOCKED`, and the loop surviving an exception |
| `python -m pytest tests`, full | 494 passed and 1 failed, in 392 s: `test_provider_wait_holds_no_database_transaction[refresh]`. Its cause: it asserts no `idle in transaction` across the whole database, and the document worker's claim shows there in 12 of 2000 samples with the worker on, and 0 of 2000 with it off. No request held a transaction. Fix: that test's app starts with `DOCUMENT_WORKER_ENABLED=0`. Rerun of `test_app_concurrency.py` and `test_app_documents.py`: 27 passed in 124 s. The full suite was not rerun, so CI is the next full run |
| `python tests/compose_smoke.py` | Passes, from clean, after the suite |
| Cross-review | Every artifact was reviewed by an assistant that did not write it (`AI-USAGE.md`) |
| `contracts/openAPI.json` | Unchanged: `git diff -- contracts/` is empty |

**Merge with Iteration 4,** at `661ee21`, after #6's CI was green on both jobs

| Check | Result |
|---|---|
| Conflicts | 21 hunks in 9 files. `app/` and `ARCHITECTURE.md` were resolved by Sonnet, `tests/` by `qa-ux`, and `docs/STATUS.md` and `AI-USAGE.md` by the Director. No conflict marker is left, and `git diff -- contracts/` is empty |
| `alembic heads` | One head, `0004` |
| First targeted run | 42 passed and 2 failed, both tests made stale by the merge. Fixed: the race test now inserts a distinct offer id (`uq_itinerary_offer`), and `test_0003_upgrades_...` asserts one linear head and the `0002 -> 0003` step |
| New tests | A confirmed booking shows its accepted price and stores only `{state, clientReference}`. Two requests either side of a poll return the same document |
| `python -m pytest tests`, full | 626 passed in 864 s. 28 of 28 contract responses exercised |
| Mutation anchors | 181 of 181 apply and compile. A123 and A152 were rewritten for the new `lifespan`. A182 and A183 are added for the merge's two new rules |
| Mutations of the touched files: A95 to A124, A152, A153, A158 to A160, A176, A182, A183 | 38 of 38 killed in 800 s, each by its `APP_KILLERS` test, with no stale entry and no false kill |
| `python tests/compose_smoke.py` | Passes, from clean, with migrations `0003` and `0004` and both loops |
| Cross-review | Codex, read-only: no implementation defect. Two staged files were older than the working tree, fixed by staging them before the commit |

**Limits accepted:** "Limits I accept" in `ARCHITECTURE.md`, the 9 that start at "A document's content lives in Postgres".


### 2.14 Iteration 6 record

Built on branch `claude/iteration-6-mcp-assistant-599778`, from `main` at `eab8575`, in parallel with Iteration 7 (2.13, pull request #10). Committed at the owner's request, with no pull request yet. The spec is `docs/iteration-6-brief.md`, including its "As built" and "After cross-review" paragraphs.

**Decisions taken by the owner on 2026-11-08 at about 04:30 (UTC-3),** all five as recommended, each probed first on `mcp` 2.3.0.

| # | Decision | Outcome | Probe |
|---|---|---|---|
| D1 | SDK and transport | `mcp==2.3.0` (`MCPServer`), Streamable HTTP, stateless with JSON, mounted at `/mcp`. The assistant is an MCP client over `httpx2.ASGITransport`, in process, with `cache=None` | The caller's `Authorization` reached the tool through `ctx.headers`. The in-process `Client(server)` carried no headers. Sync tools ran in a worker thread. The DNS-rebinding guard admits loopback hosts with a port only |
| D2 | LLM and deterministic mode | `ASSISTANT_MODE=deterministic` by default, a fixed policy with no model. `anthropic` runs `claude-sonnet-5-5` over the same tools, with the server-side refusal fallback | `anthropic` 2.6.0 resolved next to `mcp` 2.3.0 and the pinned FastAPI. No key exists on this machine |
| D3 | Identity and permissions | The API's bearer token, a 401 guard before MCP, and `membership()` shared with `trip_member` | The `/openapi.json` walk does not see a `Mount`, so `/mcp` has its own tests |
| D4 | Tools | `list_trips`, `get_trip`, `get_itinerary` (with budget and booking states), `list_documents`, `search_options`, all `readOnlyHint` | None needed |
| D5 | Evaluations | Cases E1 to E7, seven binary checks, 100% required in deterministic mode, model mode on request | None needed |

**What exists:**

- `app/`: `mcp/server.py` (five tools and one argument boundary), `assistant/assistant.py` (both modes), `api/assistant.py` (`POST /trips/{tripId}/assistant`), `membership()` and `bearer_user()` in `api/deps.py`, the `/mcp` mount and guard and the session manager in the lifespan in `main.py`, `ASSISTANT_MODE` and `ASSISTANT_MODEL` in `config.py`, and three pins in `requirements.txt` (`mcp`, `anthropic`, `httpx2`). No migration: the assistant stores nothing.
- `tests/`: `test_app_mcp.py`, `test_app_assistant.py`, `assistant_evals.py` (the runner, also a CLI), `test_assistant_evals.py`, two helpers in `booking_support.py`, and A184 to A222 in `mutation_check.py`, each with its killer in `APP_KILLERS`.
- Docs: `docs/iteration-6-brief.md`, the MCP layer and assistant section and nine limits in `ARCHITECTURE.md`, the Assistant and MCP and Evaluations sections in `README.md`, and the log entry in `AI-USAGE.md`.
- Configuration: `docker-compose.yml` and `.env.example` pass `ASSISTANT_MODE`, `ASSISTANT_MODEL` and, only when set, `ANTHROPIC_API_KEY`.

**Coordination with Iteration 7:** by session message. The route and the response shape were sent as soon as D2 was approved. Heavy jobs took turns: each session said when its job started and ended.

**Cross-review:** one round per artifact, recorded in `AI-USAGE.md`. Codex reviewed `app/` and found 0 blockers, 3 majors and 2 minors. Two majors were reproduced by the Director before routing, and all were fixed or recorded as limits. `qa-lead` reviewed Codex's tests and found 2 blockers, 5 majors and 11 minors, all applied. Codex hit its usage limit at 05:00, so `qa-ux` took its role at once.

**Required tests,** from the brief

| # | Required test | Where |
|---|---|---|
| 1 | `/mcp` and `/mcp/` refuse a missing or wrong token with a 401 in the `Error` shape; exactly five read-only tools | `test_app_mcp.py::test_mcp_without_a_token_or_with_a_wrong_one_is_401_in_the_error_shape`, `::test_mcp_lists_exactly_five_read_only_tools` |
| 2 | Every role gets the route's data; a nonparticipant, a missing trip and a malformed id get identical `NOT_FOUND`; another user's trip id grants nothing | `test_app_mcp.py::test_trip_tools_match_routes_for_every_role_and_hide_every_kind_of_unknown_trip` |
| 3 | `list_trips` shows only the caller's trips | `test_app_mcp.py::test_list_trips_returns_only_the_callers_memberships` |
| 4 | No tool writes: version, items, attempt and document rows unchanged, no booking at the provider | `test_app_mcp.py`, the state test with a booked item and a document |
| 5 | Failed legs are results, not errors; every bad argument is `VALIDATION_FAILED` and never echoes the input | `test_app_mcp.py`, the provider-down and bad-argument tests |
| 6 | The route refuses like every trip route, validates the question, and serves viewers | `test_app_assistant.py::test_assistant_route_permissions_validation_and_viewer_access` |
| 7 | `toolCalls` are the real calls, tied to the provider's search count | `test_app_assistant.py::test_tool_calls_record_the_real_mcp_calls_and_the_provider_is_searched_once_per_search_call` |
| 8 | The route changes no state | `test_app_assistant.py`, the state test |
| 9 | Model mode: no credentials or a model error is 503; an invalid mode stops startup; only `end_turn` answers | `test_app_assistant.py`, the model-mode tests against a stub server |
| E1 to E7 | The evaluations, 16 (case, check) results at 100%, and a control per defect for each check | `test_assistant_evals.py` |

**Evidence**

| Check | Result |
|---|---|
| `python -m pytest tests`, before the iteration | 626 passed, in 981 s, on `main` at `eab8575` |
| `python -m pytest tests`, final | 679 passed, in 1663 s. 28 of 28 contract responses exercised. One warning: a connection reset in a stub thread of an Iteration 4 test |
| The new files alone, first run | 4 failed, 13 passed: 3 by the SDK's error prefix, 1 by a test helper that assumes `ana`. Final: 53 passed |
| `python tests/assistant_evals.py` (deterministic) | 16 of 16 (case, check) results pass, inside the suite |
| Check controls | 26 passed, in 1.5 s, with no processes |
| Mutation sample, timed first (rule 10) | A184 in 21 s, A205 in 19 s |
| `python tests/mutation_check.py app A184 … A222` | 39 of 39 killed in 796 s, each by its `APP_KILLERS` test, with no stale entry and no false kill. Unmutated rules are limits in `ARCHITECTURE.md` |
| Findings reproduced before routing | `POST /mcp` with no token answered 404; a list for `category` answered the SDK's text with `input_value`. Both fixed, killed by A187 and A197 |
| Image built, then `python tests/compose_smoke.py` | Passes, from clean, with the new pins installed in the image. The smoke does not call the assistant: the suite covers it over real processes |
| Model mode against the real API | Not run: no key on this machine. Its cost and score are open (item 2 below) |
| `contracts/openAPI.json` | `git diff -- contracts/` is empty |
| GitHub Actions | Not run: the branch has no pull request yet |

**Open for the owner**

1. Open the pull request into `main` with Auto-fix on. #10 (Iteration 7) merges after this one, per the plan, and its assistant panel after both.
2. Model mode with a real key: run `tests/assistant_evals.py --mode anthropic` once, and record its cost and score here.

**Limits accepted:** "Limits I accept" in `ARCHITECTURE.md`, the nine that start at "MCP protocol errors keep JSON-RPC's shape", and the `ApiError` limit, rewritten.
