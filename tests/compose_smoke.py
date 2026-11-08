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

        trip_body = json.dumps(
            {
                "destinations": ["Lisbon"],
                "startsOn": "2030-06-10",
                "endsOn": "2030-06-13",
                "budgetUsd": "1000.00",
                "travelers": 2,
            }
        ).encode()
        status, created = call(
            "/trips", trip_body, base_url=APP_BASE_URL, default_headers=APP_HEADERS
        )
        assert status == 201, f"trip creation answered {status}"
        trip = json.loads(created)

        search_body = json.dumps({"category": "accommodation"}).encode()
        status, searched = call(
            f"/trips/{trip['id']}/search",
            search_body,
            base_url=APP_BASE_URL,
            default_headers=APP_HEADERS,
        )
        assert status == 200, f"application search answered {status}"
        leg = json.loads(searched)["legs"][0]
        assert leg["status"] == "complete" and leg["options"], "packaged app did not reach the mock"

        bookable = next(option for option in leg["options"] if option["bookable"])
        add_body = json.dumps(
            {"version": trip["version"], "offerId": bookable["offer"]["id"]}
        ).encode()
        status, itinerary_raw = call(
            f"/trips/{trip['id']}/itinerary/items",
            add_body,
            base_url=APP_BASE_URL,
            default_headers=APP_HEADERS,
        )
        assert status == 201, f"itinerary add answered {status}"
        itinerary = json.loads(itinerary_raw)
        assert len(itinerary["items"]) == 1
        status, current_trip_raw = call(
            f"/trips/{trip['id']}", base_url=APP_BASE_URL, default_headers=APP_HEADERS
        )
        assert status == 200
        current_trip = json.loads(current_trip_raw)
        assert current_trip["version"] == trip["version"] + 1

        compose("kill", "app")
        compose("rm", "-f", "app")
        compose("up", "-d", "--wait", "app")
        status, persisted_itinerary = call(
            f"/trips/{trip['id']}/itinerary",
            base_url=APP_BASE_URL,
            default_headers=APP_HEADERS,
        )
        assert status == 200, f"itinerary read answered {status} after replacing app"
        assert json.loads(persisted_itinerary) == itinerary, "itinerary changed after replacing app"

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
        assert json.loads(persisted) == current_trip, "trip changed after replacing app and database containers"

        status, _ = call("/__control/reset", b"")
        assert status == 404, f"control interface answered {status}, it must be off by default"
    finally:
        compose("down", "-v")
    print("compose smoke passed")


if __name__ == "__main__":
    main()
