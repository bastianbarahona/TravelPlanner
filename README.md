# Travel planning platform

A collaborative trip planner. People plan together (trips, participants, search, an itinerary with a budget), and the
application books items through an external provider that it reaches only over its public contract
(`contracts/openAPI.json`). A booking is never assumed: the application records the attempt before it asks, and reconciles
with the provider until it knows the outcome.

This is a skeleton, written in Iteration 4. Each later iteration fills in its section.

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
docker compose -p travelplanner-e2e down -v   # from the repository root
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

## More

- `ARCHITECTURE.md`: decisions, alternatives, guarantees, limits and risks.
- `docs/STATUS.md`: where the project stands, iteration by iteration.
- `provider-mock/README.md`: the provider mock, its fixtures and its control interface.
- `docs/CONTRACT_NOTES.md`: how the contract is read, and the gaps it leaves.

## Not written yet

Setup beyond Docker, the proposed deployment and the evaluations arrive with their iterations.
