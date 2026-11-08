# Browser tests

From the repository root, start the isolated stack:

```bash
docker compose -p travelplanner-e2e -f docker-compose.yml -f web/e2e/compose.e2e.yml up -d --build --wait
```

Then run Playwright from `web/`:

```bash
cd web
npm run e2e
```

The e2e stack binds the host ports 8080 (web), 8000 (app) and 4010 (provider mock control). It cannot run beside another stack on those ports, such as a default `docker compose up` or `tests/compose_smoke.py`. Stop the other stack first.
