"""Runs the mock as a real process and talks to it over real HTTP."""
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from uuid import uuid4

import httpx

from contract import ROOT, check_response

# mutation_check.py points this at a mutated copy of the mock.
APP_DIR = os.environ.get("MOCK_APP_DIR", str(ROOT / "provider-mock"))

TOKEN_A = "token-a"
TOKEN_B = "token-b"
PENDING_DELAY_SECONDS = 5
SUPPLIER_DEADLINE_SECONDS = 0.4

# Plain HTTP to localhost. Without verify=False, each client spends 0.7 s loading CA certificates.
HTTP_OPTIONS = {"timeout": 15, "verify": False, "trust_env": False}

exercised = set()


class MockProcess:
    def __init__(self, db_path, **env):
        self.env = {
            **os.environ,
            "MOCK_DB_PATH": str(db_path),
            "MOCK_TOKENS": f"{TOKEN_A}:caller-a,{TOKEN_B}:caller-b",
            "MOCK_SUPPLIER_DEADLINE_SECONDS": str(SUPPLIER_DEADLINE_SECONDS),
            "MOCK_PENDING_DELAY_SECONDS": str(PENDING_DELAY_SECONDS),
            **env,
        }
        self.process = None

    def start(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--app-dir", APP_DIR, "--port", str(port)],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{self.url}/health", **HTTP_OPTIONS).status_code == 200:
                    return
            except httpx.TransportError:
                time.sleep(0.05)
        self.kill()
        raise RuntimeError("the mock did not become ready")

    def kill(self):
        """A hard kill: no shutdown code runs, like a crash."""
        if self.process:
            self.process.kill()
            self.process.wait()
            self.process = None


def check_every_response(response):
    response.read()
    request = response.request
    path = request.url.raw_path.decode().partition("?")[0].removeprefix("/v1")
    operation = check_response(request.method, path, response.status_code, response.headers, response.content)
    exercised.add((operation, response.status_code))


def provider_client(base_url, token):
    """Every response through this client is checked against the contract."""
    return httpx.Client(
        base_url=f"{base_url}/v1",
        headers={"Authorization": f"Bearer {token}"},
        event_hooks={"response": [check_every_response]},
        **HTTP_OPTIONS,
    )


class Control:
    """The mock's test-only interface. Application workflows never use it."""

    def __init__(self, http):
        self.http = http

    def _post(self, path, body=None):
        response = self.http.post(f"/__control/{path}", json=body)
        response.raise_for_status()
        return response.json()

    def reset(self):
        self._post("reset")

    def pin_clock(self, moment):
        self._post("clock", {"now": moment})

    def fault(self, status):
        self._post("fault", {"status": status})

    def change_offer(self, offer_id, **changes):
        return self._post(f"offers/{offer_id}", changes)


def search_body(**overrides):
    return {
        "category": "accommodation",
        "destination": "Lisbon",
        "startsOn": "2030-06-10",
        "endsOn": "2030-06-13",
        "travelers": 2,
    } | overrides


def new_key():
    return f"key-{uuid4().hex}"


def new_reference():
    return f"ref-{uuid4().hex}"


def booking_body(offer, **overrides):
    return {
        "clientReference": new_reference(),
        "offerId": offer["id"],
        "expectedOfferRevision": offer["revision"],
        "expectedPrice": offer["price"],
    } | overrides


def post_booking(client, body, key=None):
    return client.post("/bookings", json=body, headers={"Idempotency-Key": key or new_key()})


def current_offer(client, offer_id="offer-hotel-001"):
    response = client.get(f"/offers/{offer_id}")
    assert response.status_code == 200
    return response.json()


def book(client, offer_id="offer-hotel-001", key=None):
    """Book the offer at its current terms. Returns (key, body sent, booking)."""
    key = key or new_key()
    body = booking_body(current_offer(client, offer_id))
    response = post_booking(client, body, key)
    assert response.status_code == 201
    return key, body, response.json()


def shifted(timestamp, **delta):
    return (datetime.fromisoformat(timestamp) + timedelta(**delta)).isoformat()


def run_concurrently(calls):
    """Starts every call at the same moment, so the requests really overlap."""
    barrier = Barrier(len(calls))

    def run(call):
        barrier.wait()
        return call()

    with ThreadPoolExecutor(len(calls)) as pool:
        return list(pool.map(run, calls))
