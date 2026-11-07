"""Process, HTTP and database helpers for the application integration tests."""
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from contract import ROOT
from support import HTTP_OPTIONS

APP_SOURCE_ROOT = Path(os.environ.get("APP_SOURCE_ROOT", ROOT)).resolve()
APP_TOKENS = "token-ana:ana,token-ben:ben,token-cam:cam,token-dee:dee"
TOKENS = {"ana": "token-ana", "ben": "token-ben", "cam": "token-cam", "dee": "token-dee"}
ERROR_KEYS = {"code", "message", "requestId"}
REQUEST_ID = re.compile(r"request-[0-9a-f]{32}\Z")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class PostgresProcess:
    def __init__(self):
        self.name = f"travelplanner-tests-{uuid4().hex}"
        self.port = free_port()
        self.database_url = f"postgresql+psycopg://travel:travel@127.0.0.1:{self.port}/travel"
        self.process = None

    def start(self):
        self.process = subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                self.name,
                "-e",
                "POSTGRES_USER=travel",
                "-e",
                "POSTGRES_PASSWORD=travel",
                "-e",
                "POSTGRES_DB=travel",
                "-p",
                f"127.0.0.1:{self.port}:5432",
                "postgres:17-alpine",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            ready = subprocess.run(
                ["docker", "exec", self.name, "pg_isready", "-h", "127.0.0.1", "-U", "travel", "-d", "travel"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if ready.returncode == 0:
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.1)
        self.kill()
        raise RuntimeError("Postgres did not become ready")

    def kill(self):
        subprocess.run(
            ["docker", "rm", "-f", self.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if self.process:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None


def run_alembic(database_url, *args):
    env = {**os.environ, "APP_DATABASE_URL": database_url, "APP_TOKENS": APP_TOKENS}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(APP_SOURCE_ROOT / "app" / "db" / "alembic.ini"),
            *args,
        ],
        cwd=APP_SOURCE_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


class AppProcess:
    def __init__(self, database_url, **env):
        self.env = {
            **os.environ,
            "APP_DATABASE_URL": database_url,
            "APP_TOKENS": APP_TOKENS,
            **env,
        }
        self.process = None

    def start(self):
        port = free_port()
        self.url = f"http://127.0.0.1:{port}"
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--app-dir",
                str(APP_SOURCE_ROOT),
                "--port",
                str(port),
            ],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{self.url}/health", **HTTP_OPTIONS)
                if response.status_code == 200:
                    try:
                        healthy = response.json() == {"status": "ok"}
                    except ValueError:
                        healthy = False
                    if healthy:
                        return
                    self.kill()
                    raise RuntimeError("the application returned the wrong health body")
            except httpx.TransportError:
                pass
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        self.kill()
        raise RuntimeError("the application did not become ready")

    def kill(self):
        """A hard kill: no application shutdown code runs."""
        if self.process:
            self.process.kill()
            self.process.wait()
            self.process = None


def check_app_response(response):
    if response.status_code < 400:
        return
    response.read()
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert isinstance(body, dict)
    assert ERROR_KEYS <= body.keys() <= ERROR_KEYS | {"details"}
    assert isinstance(body["code"], str) and body["code"]
    assert isinstance(body["message"], str) and body["message"]
    assert REQUEST_ID.fullmatch(body["requestId"])
    assert "detail" not in body
    if "details" in body:
        assert isinstance(body["details"], dict)


def app_client(base_url, user="ana"):
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {TOKENS[user]}"},
        event_hooks={"response": [check_app_response]},
        **HTTP_OPTIONS,
    )


def valid_trip_body(**overrides):
    starts_on = datetime.now(timezone.utc).date() + timedelta(days=30)
    return {
        "destinations": ["Lisbon", "Porto"],
        "startsOn": starts_on.isoformat(),
        "endsOn": (starts_on + timedelta(days=3)).isoformat(),
        "budgetUsd": "420.10",
    } | overrides


def create_trip(client, **overrides):
    body = valid_trip_body(**overrides)
    response = client.post("/trips", json=body)
    assert response.status_code == 201, response.text
    trip = response.json()
    assert trip == {
        "id": trip["id"],
        **body,
        "version": 1,
        "role": "organizer",
        "participants": [{"userId": "ana", "role": "organizer"}],
    }
    return trip


def raw_json_request(client, method, path, body):
    return client.request(
        method,
        path,
        content=json.dumps(body, ensure_ascii=True).encode("ascii"),
        headers={"Content-Type": "application/json"},
    )


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert body["message"]
    return body


def assert_validation_error(response, field, sent=None):
    body = assert_error(response, 422, "VALIDATION_FAILED")
    errors = body["details"]["errors"]
    assert isinstance(errors, list) and errors
    matching = [error for error in errors if error.get("field") == field]
    assert matching, errors
    for error in errors:
        assert set(error) == {"field", "message"}
        assert error["message"]
        assert "pattern" not in error["message"].lower()
        assert "[0-9]" not in error["message"]
        assert not error["message"].startswith("Value error, ")
    if sent is not None:
        assert str(sent) not in response.text
        assert json.dumps(str(sent), ensure_ascii=False)[1:-1] not in response.text
    return body
