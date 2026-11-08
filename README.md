# Travel planning platform

A collaborative trip planner. People plan together (trips, participants, search, an itinerary with a budget), and the
application books items through an external provider that it reaches only over its public contract
(`contracts/openAPI.json`). A booking is never assumed: the application records the attempt before it asks, and reconciles
with the provider until it knows the outcome.

To see it work, follow Setup below and then `DEMO.md`, a walkthrough with the expected outcome of each scenario and the test that proves it.

## Setup from a clean clone

Prerequisites:

- Git, and Docker with Compose v2 (`docker compose`). That is all `docker compose up` needs: the images pull their own Python 3.12 and Node 24 builds.
- Python 3.12 or later, only to run the tests (CI uses 3.12). Docker must also run for them.
- Node only to run the web unit tests and the browser tests. `web/package.json` declares no `engines`. CI and the web image build on Node 24, and the pinned versions are chosen to also run on Node 21 (`ARCHITECTURE.md`, Frontend).

Steps:

1. `git clone <repository-url>` and `cd` into it.
2. `docker compose up --build`. The first build takes a few minutes.
3. Wait until `app` and `web` report healthy (`docker compose ps`). The `migrate` service runs once and exits with code 0.
4. Open `http://localhost:8080` and pick an identity in the selector: `ana`, `ben`, `cam` or `dee` (test identities, below).
5. `docker compose down` stops the stack and keeps the data (the `app-db-data` and `provider-mock-data` volumes). `docker compose down -v` also deletes the volumes, which wipes every trip and booking.

Ports 8080, 8000 and 4010 must be free (`WEB_PORT`, `APP_PORT` and `MOCK_PORT` change them).

## Run

```bash
docker compose up --build
```

- Web: `http://localhost:8080` (`WEB_PORT` changes it)
- Application: `http://localhost:8000` (`GET /ready`, `GET /openapi.json`)
- Provider mock: `http://localhost:4010/v1`, token `travel-demo-token`
- Test identities (there is no users table): `token-ana`, `token-ben`, `token-cam` and `token-dee`, as the users `ana`,
  `ben`, `cam` and `dee`. Send one as `Authorization: Bearer <token>`.

The `migrate` service applies the database migrations once and exits, and the application waits for it. Configuration is
environment only: `docker-compose.yml` lists the application's and the mock's variables, with their defaults
(`provider-mock/README.md`). The booking loop has two application defaults that Compose does not set,
`BOOKING_POLL_INTERVALS` (`5,10,30,60` seconds) and `BOOKING_POLL_ENABLED` (`1`). To override them under Compose, add them
to the `app` service's `environment` in `docker-compose.yml`.

## Web

A single-page frontend in `web/` (React, TypeScript and Vite) served by nginx. Open `http://localhost:8080`, pick an identity in the selector, and plan. The identities are the four test users above, as the tokens `token-ana`, `token-ben`, `token-cam` and `token-dee`. The choice is kept per browser tab. The page calls `/api` on its own origin, and nginx forwards it to the application, so there is no CORS to configure.

Unit tests, with Vitest and Testing Library (no stack needed):

```bash
cd web
npm ci
npm test            # also: npm run typecheck, npm run build
```

Browser tests, with Playwright, against a separate compose stack whose provider mock has its control interface on. They need the host ports 8080, 8000 and 4010 free:

```bash
docker compose -p travelplanner-e2e -f docker-compose.yml -f web/e2e/compose.e2e.yml up -d --build --wait
cd web
npx playwright install chromium   # once
npm run e2e
cd ..
docker compose -p travelplanner-e2e down -v
```

For development, `npm run dev` in `web/` serves the page with hot reload and proxies `/api` to `http://localhost:8000`, so start the application first (`docker compose up --build`, or the `app` service alone).

## Documents

A trip document is an HTML file generated from a snapshot of the trip taken when it is requested (US-09, US-10). A
background worker renders it, so a request answers at once and the file arrives a moment later. Routes, all under
`/trips/{tripId}/documents`:

| Route | Who | Answer |
|---|---|---|
| `POST` | editors | `202` with the document, or `200` with the existing one when the trip has not changed |
| `GET` | members | the trip's documents, newest first |
| `GET /{documentId}` | members | one document: `waiting`, `in_progress`, `complete` or `failed` |
| `GET /{documentId}/content` | members | the HTML file, only when `complete`, otherwise `409` |
| `POST /{documentId}/retry` | editors | puts a `failed` document back in the queue |

Two variables, both in `docker-compose.yml`: `DOCUMENT_WORKER_ENABLED` (`1`; `0` leaves every document as stored) and
`DOCUMENT_LEASE_SECONDS` (`30`; how long a worker holds a document before another may take it). How the snapshot, the
file and its hash stay consistent is in `ARCHITECTURE.md`, Documents.

## Assistant and MCP

A read-only assistant answers questions about a trip, using only what the application's MCP tools return (US-11,
US-12). It never changes the itinerary and never books.

| Route | Who | Answer |
|---|---|---|
| `POST /trips/{tripId}/assistant` with `{"question": "..."}` | members | `{tripId, mode, answer, toolCalls}`; `toolCalls` lists every MCP call it made |
| `/mcp/` (Streamable HTTP, same bearer token) | any valid token | the MCP server: `list_trips`, `get_trip`, `get_itinerary`, `list_documents`, `search_options` |

Two variables, both in `docker-compose.yml`:

- `ASSISTANT_MODE`: `deterministic` (the default, no model, no key) or `anthropic` (Claude through the Anthropic API).
- `ASSISTANT_MODEL`: `claude-sonnet-5-5` by default, used only in `anthropic` mode, which also needs
  `ANTHROPIC_API_KEY`.

How identity, permissions and both modes work is in `ARCHITECTURE.md`, MCP layer and assistant.

## Evaluations

Fixed cases (E1 to E7) score the assistant with binary checks: the right tools were called, every price and offer id in
the answer came from a tool, "no suitable option" when nothing fits, failures and issues are stated, outsiders are
refused, and nothing changes.

```bash
.venv/Scripts/python tests/assistant_evals.py                      # deterministic: no model, no cost, must be 100%
.venv/Scripts/python -m pytest tests/test_assistant_evals.py       # the same, plus a control per check
ANTHROPIC_API_KEY=... .venv/Scripts/python tests/assistant_evals.py --mode anthropic   # model mode, reports scores
```

The deterministic run is part of the suite and of CI. Model mode is not: it needs a key, calls Claude for each case
(up to 8 model turns per question), and is billed by the Anthropic API. Its measured cost and score are recorded in
`docs/STATUS.md` once a key is supplied.

## Test

Every worktree has its own `.venv`. Docker must be running, because the tests start a real Postgres.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r tests/requirements.txt   # .venv/bin/python on Linux and macOS
.venv/Scripts/python -m pytest tests                            # about 13 minutes here
```

Real HTTP, with the mock and Postgres as real processes. `tests/compose_smoke.py` runs the compose stack.

## Mutation check

Proves the tests can fail: it breaks one rule at a time in a temporary copy of the source and expects `pytest` to fail.

```bash
.venv/Scripts/python tests/mutation_check.py        # everything, over an hour
.venv/Scripts/python tests/mutation_check.py app    # only the application's mutations
```

A survivor is a rule no test protects.

## Checks

CI (`.github/workflows/ci.yml`) runs on every push, in three jobs:

- `contract-tests`: `pip install -r tests/requirements.txt`, then `python -m pytest tests` (Python 3.12, 20 minutes at most).
- `compose-smoke`: `python3 tests/compose_smoke.py` against the compose stack (10 minutes at most).
- `web`: in `web/`, `npm ci`, `npm run typecheck`, `npm test` and `npm run build` (Node 24, 10 minutes at most).

The contract is immutable. `git diff -- contracts/` must print nothing, and `tests/test_contract_checks.py` fails when the SHA-256 of `contracts/openAPI.json` changes. The browser tests and the mutation check are manual, and are not in CI.

## Deployment (proposed)

Not deployed. The proposal is Azure Container Apps: `web` public, `app` and the provider internal, `migrate` as a job run before each revision, a managed PostgreSQL, secrets in Key Vault. `ARCHITECTURE.md`, Deployment (proposed, not deployed), holds the reasoning, the rejected options and the changes it needs.

## More

- `DEMO.md`: the main workflow, and the failure and concurrency scenarios, step by step.

- `ARCHITECTURE.md`: decisions, alternatives, guarantees, limits and risks.
- `docs/STATUS.md`: where the project stands, iteration by iteration.
- `provider-mock/README.md`: the provider mock, its fixtures and its control interface.
- `docs/CONTRACT_NOTES.md`: how the contract is read, and the gaps it leaves.
