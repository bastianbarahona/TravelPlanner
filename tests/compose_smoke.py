"""Proves the packaged mock: a booking survives a crash and a new container.

Not collected by pytest. Needs Docker and a free port 4010. Run from the repository root:
python tests/compose_smoke.py

`docker compose restart` and `kill` reuse the container, whose own filesystem would keep
the data even with no volume. Only a new container proves the volume. Standard library
only, so CI needs no install. Its own project name keeps `down -v` away from local data.
"""
import json
import subprocess
import urllib.error
import urllib.request

BASE_URL = "http://localhost:4010"
HEADERS = {"Authorization": "Bearer travel-demo-token", "Content-Type": "application/json"}


def compose(*args):
    subprocess.run(["docker", "compose", "-p", "travelplanner-smoke", *args], check=True)


def call(path, body=None, headers=None):
    """Returns (status, raw body). urllib raises on 4xx, which is a normal answer here."""
    request = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers={**HEADERS, **(headers or {})})
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

        compose("kill")
        compose("down")  # keeps the volume and removes the container
        compose("up", "-d", "--wait")

        status, replay = call("/v1/bookings", body, key)
        assert status == 200, f"replay in a new container answered {status}, so the booking was lost"
        assert replay == first, "replay differs from the first response"

        status, _ = call("/__control/reset", b"")
        assert status == 404, f"control interface answered {status}, it must be off by default"
    finally:
        compose("down", "-v")
    print("compose smoke passed")


if __name__ == "__main__":
    main()
