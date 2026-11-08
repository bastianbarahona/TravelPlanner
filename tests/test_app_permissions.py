"""Requirements 4, 5 and 6: route-wide authentication and trip permissions."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from app_support import (
    APP_SOURCE_ROOT,
    APP_TOKENS,
    app_client as make_app_client,
    add_participant,
    assert_error,
    assert_validation_error,
    check_app_response,
    create_trip,
    free_port,
    valid_trip_body,
)
from support import HTTP_OPTIONS

PUBLIC_SCHEMA_ROUTES = {("GET", "/health"), ("GET", "/ready")}
PUBLIC_NON_SCHEMA_ROUTES = {("GET", "/openapi.json")}


def concrete_path(template, trip_id, user_id="ben", item_id=None):
    return (
        template.replace("{tripId}", str(trip_id))
        .replace("{userId}", user_id)
        .replace("{itemId}", str(item_id or uuid4()))
    )


def schema_operations(spec):
    methods = {"get", "post", "put", "patch", "delete"}
    return [
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in methods
    ]


def test_ready_answers_for_the_test_database_without_authentication(start_app):
    application = start_app(PROVIDER_BASE_URL=f"http://127.0.0.1:{free_port()}/v1")
    with httpx.Client(
        base_url=application.url,
        event_hooks={"response": [check_app_response]},
        **HTTP_OPTIONS,
    ) as anonymous:
        response = anonymous.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_unreachable_database_fails_readiness_but_not_liveness(start_app):
    database_url = (
        "postgresql+psycopg://travel:travel@127.0.0.1:"
        f"{free_port()}/travel?connect_timeout=1"
    )
    process = start_app(APP_DATABASE_URL=database_url)
    with httpx.Client(
        base_url=process.url,
        event_hooks={"response": [check_app_response]},
        **HTTP_OPTIONS,
    ) as anonymous:
        health = anonymous.get("/health")
        ready = anonymous.get("/ready")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert_error(ready, 503, "NOT_READY")


def test_openapi_walk_enforces_auth_and_nonparticipant_hiding(application, empty_database):
    with httpx.Client(
        base_url=application.url,
        event_hooks={"response": [check_app_response]},
        **HTTP_OPTIONS,
    ) as anonymous:
        spec_response = anonymous.get("/openapi.json")
        assert spec_response.status_code == 200
        spec = spec_response.json()

        for method, template in schema_operations(spec):
            if (method, template) in PUBLIC_SCHEMA_ROUTES:
                response = anonymous.request(method, template, json={})
                assert response.status_code == 200
                continue
            path = concrete_path(template, uuid4())
            response = anonymous.request(method, path, json={})
            assert_error(response, 401, "UNAUTHORIZED")
            assert response.headers["www-authenticate"] == "Bearer"

    with make_app_client(application.url) as organizer:
        trip = create_trip(organizer)
    with make_app_client(application.url, "cam") as outsider:
        for method, template in schema_operations(spec):
            if "{tripId}" not in template:
                continue
            response = outsider.request(method, concrete_path(template, trip["id"]), json={})
            assert_error(response, 404, "NOT_FOUND")


def test_only_malformed_json_precedes_authentication_and_trip_hiding(
    app_client, application
):
    trip = create_trip(app_client)
    malformed_bodies = (b'{"version":', b'{"destinations":["\xff"]}')
    with httpx.Client(
        base_url=application.url,
        event_hooks={"response": [check_app_response]},
        **HTTP_OPTIONS,
    ) as anonymous:
        for malformed in malformed_bodies:
            response = anonymous.post(
                "/trips", content=malformed, headers={"Content-Type": "application/json"}
            )
            assert_validation_error(response, "body")
        for content, content_type in ((b"", "application/json"), (b"anything", "text/plain")):
            response = anonymous.post(
                "/trips", content=content, headers={"Content-Type": content_type}
            )
            assert_error(response, 401, "UNAUTHORIZED")

    with make_app_client(application.url, "cam") as outsider:
        for malformed in malformed_bodies:
            response = outsider.patch(
                f"/trips/{trip['id']}",
                content=malformed,
                headers={"Content-Type": "application/json"},
            )
            assert_validation_error(response, "body")
        for content, content_type in ((b"", "application/json"), (b"anything", "text/plain")):
            response = outsider.patch(
                f"/trips/{trip['id']}",
                content=content,
                headers={"Content-Type": content_type},
            )
            assert_error(response, 404, "NOT_FOUND")


def test_no_runtime_route_can_hide_from_openapi(application, empty_database):
    with httpx.Client(base_url=application.url, **HTTP_OPTIONS) as client:
        spec = client.get("/openapi.json").json()
    declared = set(schema_operations(spec))
    script = """
import json
from app.main import app
from fastapi.routing import iter_route_contexts
methods = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}
print(json.dumps(sorted((method, route.path) for route in iter_route_contexts(app.router.routes) for method in (route.methods or set()) & methods)))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=APP_SOURCE_ROOT,
        env={
            **os.environ,
            "APP_DATABASE_URL": (
                "postgresql+psycopg://travel:travel@127.0.0.1:1/unreachable?connect_timeout=1"
            ),
            "APP_TOKENS": APP_TOKENS,
            "PROVIDER_BASE_URL": "http://127.0.0.1:1/v1",
            "PROVIDER_TOKEN": "token-a",
            "PYTHONPATH": str(APP_SOURCE_ROOT),
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    runtime = {tuple(item) for item in json.loads(result.stdout)}
    assert runtime - PUBLIC_NON_SCHEMA_ROUTES == declared


@pytest.mark.parametrize("role", ["viewer", "editor"])
@pytest.mark.parametrize("operation", ["trip", "add", "change", "remove"])
def test_every_organizer_write_refuses_viewers_and_editors_before_validation(
    app_client, application, role, operation
):
    trip = create_trip(app_client)
    add_participant(app_client, trip["id"], "ben", role)
    if operation in {"change", "remove"}:
        add_participant(app_client, trip["id"], "cam")
    before = app_client.get(f"/trips/{trip['id']}").json()

    with make_app_client(application.url, "ben") as actor:
        assert actor.get(f"/trips/{trip['id']}").status_code == 200
        listed = actor.get("/trips")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["trips"]] == [trip["id"]]
        if operation == "trip":
            response = actor.patch(f"/trips/{trip['id']}", json={"version": "invalid"})
        elif operation == "add":
            response = actor.post(
                f"/trips/{trip['id']}/participants",
                json={"userId": "not-configured", "role": "organizer"},
            )
        elif operation == "change":
            response = actor.patch(
                f"/trips/{trip['id']}/participants/cam", json={"role": "organizer"}
            )
        else:
            response = actor.delete(f"/trips/{trip['id']}/participants/cam")
    assert_error(response, 403, "FORBIDDEN")
    assert app_client.get(f"/trips/{trip['id']}").json() == before


def test_organizer_can_use_every_trip_write_route(app_client):
    trip = create_trip(app_client)
    changed = app_client.patch(
        f"/trips/{trip['id']}", json={"version": 1, "destinations": ["Santiago"]}
    )
    assert changed.status_code == 200
    assert changed.json()["destinations"] == ["Santiago"]
    assert changed.json()["version"] == 2

    add_participant(app_client, trip["id"], "ben")
    promoted = app_client.patch(
        f"/trips/{trip['id']}/participants/ben", json={"role": "editor"}
    )
    assert promoted.status_code == 200
    assert promoted.json() == {"userId": "ben", "role": "editor"}
    assert app_client.get(f"/trips/{trip['id']}").json()["version"] == 2

    removed = app_client.delete(f"/trips/{trip['id']}/participants/ben")
    assert removed.status_code == 204
    assert removed.content == b""
    current = app_client.get(f"/trips/{trip['id']}").json()
    assert current["version"] == 2
    assert current["participants"] == [{"userId": "ana", "role": "organizer"}]


def test_role_change_and_removal_apply_on_the_next_request(app_client, application):
    trip = create_trip(app_client)
    add_participant(app_client, trip["id"], "ben", "viewer")
    with make_app_client(application.url, "ben") as ben:
        assert ben.get(f"/trips/{trip['id']}").json()["role"] == "viewer"
        changed = app_client.patch(
            f"/trips/{trip['id']}/participants/ben", json={"role": "editor"}
        )
        assert changed.status_code == 200
        assert ben.get(f"/trips/{trip['id']}").json()["role"] == "editor"

        assert app_client.delete(f"/trips/{trip['id']}/participants/ben").status_code == 204
        assert_error(ben.get(f"/trips/{trip['id']}"), 404, "NOT_FOUND")
        assert ben.get("/trips").json() == {"trips": []}


def test_nonparticipant_missing_and_malformed_trip_are_indistinguishable(
    app_client, application
):
    trip = create_trip(app_client)
    missing = uuid4()
    spec = app_client.get("/openapi.json").json()
    routes = [(method, template) for method, template in schema_operations(spec) if "{tripId}" in template]

    def without_request_id(response):
        error = assert_error(response, 404, "NOT_FOUND")
        return {key: value for key, value in error.items() if key != "requestId"}

    with make_app_client(application.url, "cam") as outsider:
        unknown_route = without_request_id(outsider.get("/route-that-does-not-exist"))
        for method, template in routes:
            normalized = []
            for value in (trip["id"], missing, "not-a-uuid", "%2F", "a%2Fb"):
                response = outsider.request(
                    method, concrete_path(template, value), json={}
                )
                normalized.append(without_request_id(response))
            assert all(error == unknown_route for error in normalized)

    braced = app_client.get(f"/trips/%7B{trip['id']}%7D")
    assert without_request_id(braced) == unknown_route


def test_organizer_row_is_unique_and_immutable(app_client):
    trip = create_trip(app_client)
    duplicate = app_client.post(
        f"/trips/{trip['id']}/participants", json={"userId": "ana", "role": "viewer"}
    )
    assert_error(duplicate, 409, "PARTICIPANT_EXISTS")

    new_organizer = app_client.post(
        f"/trips/{trip['id']}/participants", json={"userId": "ben", "role": "organizer"}
    )
    assert_validation_error(new_organizer, "role")

    changed = app_client.patch(
        f"/trips/{trip['id']}/participants/ana", json={"role": "editor"}
    )
    assert_error(changed, 409, "ORGANIZER_IMMUTABLE")
    removed = app_client.delete(f"/trips/{trip['id']}/participants/ana")
    assert_error(removed, 409, "ORGANIZER_IMMUTABLE")
    assert app_client.get(f"/trips/{trip['id']}").json() == trip


def test_unknown_identity_in_body_or_path_is_refused_safely(app_client):
    trip = create_trip(app_client)
    body = app_client.post(
        f"/trips/{trip['id']}/participants", json={"userId": "unknown", "role": "viewer"}
    )
    assert_validation_error(body, "userId")

    for user_id in ("unknown", "%00"):
        changed = app_client.patch(
            f"/trips/{trip['id']}/participants/{user_id}", json={"role": "viewer"}
        )
        assert_error(changed, 404, "NOT_FOUND")
        removed = app_client.delete(f"/trips/{trip['id']}/participants/{user_id}")
        assert_error(removed, 404, "NOT_FOUND")


def test_non_ascii_bearer_token_is_401_not_500(application, empty_database):
    headers = httpx.Headers([(b"Authorization", b"Bearer t\xc3\xb6ken")])
    with httpx.Client(
        base_url=application.url,
        headers=headers,
        event_hooks={"response": [check_app_response]},
        **HTTP_OPTIONS,
    ) as client:
        response = client.get("/trips")
    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"


def test_list_contains_only_memberships_and_orders_by_date_then_id(app_client, application):
    base = datetime.now(timezone.utc).date() + timedelta(days=20)
    first = create_trip(
        app_client,
        destinations=["First"],
        startsOn=base.isoformat(),
        endsOn=(base + timedelta(days=1)).isoformat(),
    )
    add_participant(app_client, first["id"], "ben")
    same_date = [
        create_trip(
            app_client,
            destinations=[f"Same date {index}"],
            startsOn=(base + timedelta(days=1)).isoformat(),
            endsOn=(base + timedelta(days=2)).isoformat(),
        )
        for index in range(6)
    ]

    with make_app_client(application.url, "ben") as ben:
        own_response = ben.post(
            "/trips",
            json=valid_trip_body(
                destinations=["Ben"],
                startsOn=(base + timedelta(days=2)).isoformat(),
                endsOn=(base + timedelta(days=3)).isoformat(),
            ),
        )
        assert own_response.status_code == 201
        own = own_response.json()
        ben_list = ben.get("/trips").json()["trips"]
        assert [item["id"] for item in ben_list] == [first["id"], own["id"]]
        assert [item["role"] for item in ben_list] == ["viewer", "organizer"]

    ana_list = app_client.get("/trips").json()["trips"]
    expected_same_date = sorted(trip["id"] for trip in same_date)
    assert [item["id"] for item in ana_list] == [first["id"], *expected_same_date]
    assert all(item["id"] != own["id"] for item in ana_list)
    assert [(item["startsOn"], item["id"]) for item in ana_list] == sorted(
        (item["startsOn"], item["id"]) for item in ana_list
    )


def test_participants_are_returned_in_user_id_order(app_client):
    trip = create_trip(app_client)
    for user_id in ("dee", "cam", "ben"):
        add_participant(app_client, trip["id"], user_id)
    participants = app_client.get(f"/trips/{trip['id']}").json()["participants"]
    assert [participant["userId"] for participant in participants] == ["ana", "ben", "cam", "dee"]
