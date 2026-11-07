# Provider mock

Implements `contracts/openAPI.json` as a separate HTTP service. Request bodies are validated against that file at runtime, so the mock has no second copy of the schemas. The readings used where the contract leaves a gap are in `docs/CONTRACT_NOTES.md`, Section 4.

## Run

```bash
docker compose up --build
```

- Base URL: `http://localhost:4010/v1`
- Token: `travel-demo-token`
- Health: `GET /health`, outside the contract and without a token

```bash
curl -H "Authorization: Bearer travel-demo-token" -H "Content-Type: application/json" -d '{"category":"accommodation","destination":"Lisbon","startsOn":"2030-06-10","endsOn":"2030-06-13","travelers":2}' http://localhost:4010/v1/offers/search
```

Bookings, keys, references and quotes live in a SQLite file on the `provider-mock-data` volume, so they survive restarts and crashes. Fixture edits reach only a new database:

```bash
docker compose down -v
```

## Configuration

Environment only. `docker compose` applies the defaults below, and `.env.example` lists the same values.

| Variable | Default | Meaning |
|---|---|---|
| `MOCK_DB_PATH` | required (`/data/provider-mock.db` in compose) | SQLite file |
| `MOCK_TOKENS` | required (`travel-demo-token:travel-app` in compose) | Comma-separated `token:caller` pairs. Bookings, references and keys are scoped to the caller. |
| `MOCK_QUOTE_TTL_SECONDS` | `900` | Quote lifetime, so `validUntil` is mock time plus this |
| `MOCK_SUPPLIER_DEADLINE_SECONDS` | `5` | How long a search waits for a slow supplier |
| `MOCK_PENDING_DELAY_SECONDS` | `5` | Time until a pending booking reaches its outcome |
| `MOCK_CONTROL_ENABLED` | `0` | `1` exposes the control interface. Test environments only. |

## Fixtures

Defined in `fixtures.json`. All schedules fall between `2030-06-10` and `2030-06-13`, so use that window. The party is 2 travelers, and 4 also matches `offer-hotel-006`. Locations match by exact name, ignoring case.

| Search | Result |
|---|---|
| `Lisbon` | Complete: all three categories |
| `Porto` | Partial for transport and accommodation: `supplier-b` is unavailable |
| `Faro` | Partial: `supplier-b` misses the deadline |
| `Braga` | Partial: `supplier-b` is rate limited |
| `Coimbra` | Partial with no surviving offers |
| `Evora` | Failed: `503`, every supplier is unavailable |
| `Sintra` | Failed: `503`, every supplier misses the deadline |
| `Porto` with `providerId: supplier-b` | Failed: `503` |
| `Braga` with `providerId: supplier-b` | Failed: `429` |
| Any other destination | Empty |

Transport routes are `London` to `Lisbon`, `Lisbon` to `London`, and `London` to `Porto`.

| Offer | Booking outcome |
|---|---|
| `offer-hotel-002` | `pending`, then `confirmed` |
| `offer-hotel-003` | `pending`, then `failed` |
| `offer-hotel-004` | `failed` immediately |
| `offer-hotel-005` | Unavailable, so `409 OFFER_UNAVAILABLE` |
| Any other offer | `confirmed` immediately |

`offer-activity-001` is free, with an amount of `0.00`. `offer-activity-003` starts just after local midnight, which is still the previous day in UTC.

## Control interface

Outside the contract, and absent unless `MOCK_CONTROL_ENABLED=1`. Tests use it for what changes over time. Application workflows never call it.

| Request | Effect |
|---|---|
| `POST /__control/reset` | Clears bookings, keys, references, clock and fault, and reseeds the offers |
| `POST /__control/clock` with `{"now": "2030-01-15T12:00:00Z"}` | Pins mock time. `{"now": null}` returns to the wall clock. |
| `POST /__control/fault` with `{"status": 429}` or `{"status": 503}` | Every `/v1` operation answers that status after authentication and validation. `{"status": null}` clears it. |
| `POST /__control/offers/{offerId}` with fields to merge | For example `{"available": false}`. Always creates a new revision. |

The clock and fault live in memory, so a restart clears them. Offer changes are stored.

## Tests

From the repository root, in a virtual environment:

```bash
pip install -r tests/requirements.txt
```

```bash
python -m pytest tests
```

The tests start the mock as a real process on a free port, with its own database, and talk to it over HTTP. Docker is not needed. Every response is checked against the contract and its prose-only rules, and the run ends by reporting how many declared responses it exercised.

Required tests, which `tests/mutation_check.py` uses as its numbering:

1. Every response validates against the contract, with `rfc3339-validator` installed.
2. Each prose-only rule has its own assertion (`failure`, `endsOn`, `endsAt`, `updatedAt`, `partial` with `warnings`, transport `origin`).
3. Replay returns `200` with the current state, even after the quote expires or changes.
4. Concurrent identical requests produce exactly one `201` and one booking.
5. All four 409 cases, in the order of `docs/CONTRACT_NOTES.md` 2.1.
6. Keys, references and bookings survive a mock restart.
7. Callers cannot see each other's bookings or references, and can reuse the same reference.
8. Quote expiry at the boundary: an acceptance exactly at `validUntil` is rejected.
9. Partial, complete, empty and failed searches.
10. Invalid input always returns an `Error` body, never FastAPI's default.

To prove the tests can fail:

```bash
python tests/mutation_check.py
```

It breaks one rule at a time in a temporary copy of the mock and expects the suite to fail. A full run takes about 30 minutes. Pass required test numbers, or words from a mutation's label, to run a subset, for example `python tests/mutation_check.py 8 9`.
