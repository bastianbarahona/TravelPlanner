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

- Application: `http://localhost:8000` (`GET /ready`, `GET /openapi.json`)
- Provider mock: `http://localhost:4010/v1`, token `travel-demo-token`
- Test identities (there is no users table): `token-ana`, `token-ben`, `token-cam` and `token-dee`, as the users `ana`,
  `ben`, `cam` and `dee`. Send one as `Authorization: Bearer <token>`.

The `migrate` service applies the database migrations once and exits, and the application waits for it. Configuration is
environment only: `docker-compose.yml` lists the application's and the mock's variables, with their defaults
(`provider-mock/README.md`). The booking loop has two application defaults that Compose does not set,
`BOOKING_POLL_INTERVALS` (`5,10,30,60` seconds) and `BOOKING_POLL_ENABLED` (`1`). To override them under Compose, add them
to the `app` service's `environment` in `docker-compose.yml`.

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

Setup beyond Docker, the proposed deployment, the evaluations and the documents module arrive with their iterations.
