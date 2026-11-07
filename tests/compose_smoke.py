"""Proves packaged durability: provider and application data survive replacement.

Not collected by pytest. Needs Docker and free ports 4010 and 8000. Run from the repository root:
python tests/compose_smoke.py

`docker compose restart` and `kill` reuse the container, whose own filesystem would keep
the data even with no volume. Only a new container proves the volume. Standard library
only, so CI needs no install. Its own project name keeps `down -v` away from local data.
"""
import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

PROVIDER_BASE_URL = "http://localhost:4010"
APP_BASE_URL = "http://localhost:8000"
PROVIDER_HEADERS = {"Authorization": "Bearer travel-demo-token", "Content-Type": "application/json"}
APP_HEADERS = {"Authorization": "Bearer token-ana", "Content-Type": "application/json"}


def compose(*args):
    subprocess.run(["docker", "compose", "-p", "travelplanner-smoke", *args], check=True)


def call(path, body=None, headers=None, base_url=PROVIDER_BASE_URL, default_headers=PROVIDER_HEADERS):
    """Returns (status, raw body). urllib raises on 4xx, which is a normal answer here."""
    request = urllib.request.Request(
        f"{base_url}{path}", data=body, headers={**default_headers, **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main():
    key = {"Idempotency-Key": "smoke-key"}
    compose("down", "-v")
    try:
        compose("up", "-d", "--build", "--wait")

        status, raw = call("/v1/offers/offer-hotel-001")
        assert status == 200, f"offer answered {status}"
        offer = json.loads(raw)
        body = json.dumps(
            {
                "clientReference": "smoke-reference",
                "offerId": offer["id"],
                "expectedOfferRevision": offer["revision"],
                "expectedPrice": offer["price"],
            }
        ).encode()
        status, first = call("/v1/bookings", body, key)
        assert status == 201, f"first booking answered {status}"

        starts_on = datetime.now(timezone.utc).date() + timedelta(days=30)
        trip_body = json.dumps(
            {
                "destinations": ["Lisbon", "Porto"],
                "startsOn": starts_on.isoformat(),
                "endsOn": (starts_on + timedelta(days=3)).isoformat(),
                "budgetUsd": "420.10",
            }
        ).encode()
        status, created = call(
            "/trips", trip_body, base_url=APP_BASE_URL, default_headers=APP_HEADERS
        )
        assert status == 201, f"trip creation answered {status}"
        trip = json.loads(created)

        compose("kill")
        compose("down")  # keeps the volume and removes the container
        compose("up", "-d", "--wait")

        status, replay = call("/v1/bookings", body, key)
        assert status == 200, f"replay in a new container answered {status}, so the booking was lost"
        assert replay == first, "replay differs from the first response"

        status, persisted = call(
            f"/trips/{trip['id']}", base_url=APP_BASE_URL, default_headers=APP_HEADERS
        )
        assert status == 200, f"trip read answered {status}, so the trip was lost"
        assert json.loads(persisted) == trip, "trip changed after replacing the app and database containers"

        status, _ = call("/__control/reset", b"")
        assert status == 404, f"control interface answered {status}, it must be off by default"
    finally:
        compose("down", "-v")
    print("compose smoke passed")


if __name__ == "__main__":
    main()
