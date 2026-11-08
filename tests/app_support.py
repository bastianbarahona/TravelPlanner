"""Process, HTTP and database helpers for the application integration tests."""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
        self._log_directory = tempfile.TemporaryDirectory(
            prefix="travelplanner-app-", ignore_cleanup_errors=True
        )
        self.log_path = Path(self._log_directory.name) / "application.log"
        self._log_stream = None

    def start(self):
        port = free_port()
        self.url = f"http://127.0.0.1:{port}"
        self._log_stream = self.log_path.open("ab", buffering=0)
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
            stdout=self._log_stream,
            stderr=subprocess.STDOUT,
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
        if self._log_stream:
            self._log_stream.close()
            self._log_stream = None

    def read_logs(self):
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def close(self):
        self.kill()
        self._log_directory.cleanup()


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
        "travelers": 2,
    } | overrides


def planning_trip_body(**overrides):
    return {
        "destinations": ["Lisbon"],
        "startsOn": "2030-06-10",
        "endsOn": "2030-06-13",
        "budgetUsd": "1000.00",
        "travelers": 2,
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


def create_planning_trip(client, **overrides):
    body = planning_trip_body(**overrides)
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


def add_participant(client, trip_id, user_id, role="viewer"):
    response = client.post(
        f"/trips/{trip_id}/participants", json={"userId": user_id, "role": role}
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"userId": user_id, "role": role}
    return response.json()


def stub_offer(offer_id="stub-offer", **changes):
    return {
        "id": offer_id,
        "providerId": "stub-supplier",
        "category": "accommodation",
        "title": "Stub hotel",
        "description": "A contract-valid response from the test supplier.",
        "destination": "Lisbon",
        "startsAt": "2030-06-10T15:00:00+01:00",
        "endsAt": "2030-06-13T11:00:00+01:00",
        "travelers": 2,
        "price": {"amount": "420.00", "currency": "USD"},
        "revision": "stub-revision-1",
        "available": True,
        "validUntil": "2099-01-01T00:00:00Z",
    } | changes


def stub_search_result(offer=None, **changes):
    return {
        "offers": [offer or stub_offer()],
        "partial": False,
        "warnings": [],
        "searchedAt": "2030-06-01T12:00:00Z",
    } | changes


def provider_error(code="PROVIDER_UNAVAILABLE"):
    return {"code": code, "message": "The stub refused the request.", "requestId": "stub-request-1"}


def install_delay_trigger(db_engine, name, event, table, seconds=0.3):
    with db_engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql "
                f"AS $$ BEGIN PERFORM pg_sleep({seconds}); RETURN NEW; END $$"
            )
        )
        connection.execute(
            text(
                f"CREATE TRIGGER {name} BEFORE {event} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {name}()"
            )
        )


def remove_delay_trigger(db_engine, name, table):
    with db_engine.begin() as connection:
        connection.execute(text(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
        connection.execute(text(f"DROP FUNCTION IF EXISTS {name}()"))


def assert_constraint(db_engine, statement, parameters, name):
    with pytest.raises(IntegrityError) as caught:
        with db_engine.begin() as connection:
            connection.execute(text(statement), parameters)
    assert caught.value.orig.diag.constraint_name == name


def insert_trip(db_engine, trip_id):
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO trips (id, starts_on, ends_on, budget_usd, travelers, version) "
                "VALUES (:id, '2030-06-10', '2030-06-13', 1.00, 2, 1)"
            ),
            {"id": trip_id},
        )


@dataclass
class StubResponse:
    status: int = 200
    body: object = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    chunks: list[bytes] | None = None
    delay: float = 0.0
    disconnect: bool = False


class ProviderStub:
    """Small programmable real HTTP server for provider failures the mock cannot emit."""

    def __init__(self, responder):
        self.responder = responder
        self.requests = []
        self.exceptions = []
        self.condition = threading.Condition()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                self._handle()

            def do_POST(self):
                self._handle()

            def _handle(self):
                length = int(self.headers.get("content-length", "0"))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw) if raw else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = raw
                request = {
                    "method": self.command,
                    "path": self.path,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": body,
                    "raw_body": raw,
                }
                with owner.condition:
                    owner.requests.append(request)
                    owner.condition.notify_all()
                try:
                    reply = owner.responder(request)
                except BaseException as error:
                    with owner.condition:
                        owner.exceptions.append(error)
                        owner.condition.notify_all()
                    self.close_connection = True
                    return
                if reply.disconnect:
                    self.close_connection = True
                    return
                if reply.chunks is not None:
                    payload = b"".join(reply.chunks)
                elif isinstance(reply.body, bytes):
                    payload = reply.body
                else:
                    payload = json.dumps(reply.body).encode()
                self.send_response(reply.status)
                headers = {key.lower(): value for key, value in reply.headers.items()}
                if "content-type" not in headers:
                    headers["content-type"] = (
                        "application/octet-stream" if isinstance(reply.body, bytes) else "application/json"
                    )
                headers.setdefault("content-length", str(len(payload)))
                for key, value in headers.items():
                    self.send_header(key, str(value))
                self.end_headers()
                try:
                    if reply.chunks is None:
                        self.wfile.write(payload)
                    else:
                        for chunk in reply.chunks:
                            time.sleep(reply.delay)
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, _format, *_args):
                pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            request_queue_size = 128

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.exceptions:
            raise self.exceptions[0]

    def wait_for_requests(self, count, timeout=5):
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.requests) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
        return True


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
