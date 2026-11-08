"""Requirement 11: every error source uses the single application Error shape."""
import httpx

from app_support import (
    add_participant,
    app_client as make_app_client,
    assert_error,
    assert_validation_error,
    check_app_response,
    create_trip,
    valid_trip_body,
)
from support import HTTP_OPTIONS


def test_401_variants_and_public_health(application, empty_database):
    with httpx.Client(
        base_url=application.url,
        event_hooks={"response": [check_app_response]},
        **HTTP_OPTIONS,
    ) as client:
        assert client.get("/health").json() == {"status": "ok"}
        for headers in ({}, {"Authorization": "Bearer bad"}, {"Authorization": "Basic token-ana"}):
            response = client.get("/trips", headers=headers)
            assert_error(response, 401, "UNAUTHORIZED")
            assert response.headers["www-authenticate"] == "Bearer"


def test_403_404_unknown_route_and_405_have_exact_codes(app_client, application):
    trip = create_trip(app_client)
    add_participant(app_client, trip["id"], "ben")
    with make_app_client(application.url, "ben") as viewer:
        forbidden = viewer.patch(
            f"/trips/{trip['id']}", json={"version": 1, "budgetUsd": "1.00"}
        )
        assert_error(forbidden, 403, "FORBIDDEN")

    assert_error(app_client.get(f"/trips/{'0' * 32}"), 404, "NOT_FOUND")
    assert_error(app_client.get("/route-that-does-not-exist"), 404, "NOT_FOUND")
    assert_error(app_client.post("/health", json={}), 405, "METHOD_NOT_ALLOWED")
    missing_participant = app_client.patch(
        f"/trips/{trip['id']}/participants/cam", json={"role": "viewer"}
    )
    assert_error(missing_participant, 404, "NOT_FOUND")


def test_each_conflict_code_uses_error_shape(app_client):
    trip = create_trip(app_client)
    changed = app_client.patch(
        f"/trips/{trip['id']}", json={"version": 1, "budgetUsd": "2.00"}
    )
    assert changed.status_code == 200

    stale = app_client.patch(
        f"/trips/{trip['id']}", json={"version": 1, "budgetUsd": "3.00"}
    )
    assert_error(stale, 409, "VERSION_CONFLICT")

    exists = app_client.post(
        f"/trips/{trip['id']}/participants", json={"userId": "ana", "role": "viewer"}
    )
    assert_error(exists, 409, "PARTICIPANT_EXISTS")

    immutable = app_client.delete(f"/trips/{trip['id']}/participants/ana")
    assert_error(immutable, 409, "ORGANIZER_IMMUTABLE")


def test_schema_domain_and_body_decoding_failures_are_validation_errors(app_client):
    schema = app_client.post("/trips", json=valid_trip_body(budgetUsd="420.1"))
    assert_validation_error(schema, "budgetUsd")

    body = valid_trip_body()
    domain = app_client.post(
        "/trips", json=body | {"startsOn": body["endsOn"], "endsOn": body["startsOn"]}
    )
    assert_validation_error(domain, "body")

    malformed = app_client.post(
        "/trips", content=b'{"destinations":', headers={"Content-Type": "application/json"}
    )
    assert_validation_error(malformed, "body")

    invalid_utf8 = app_client.post(
        "/trips",
        content=b'{"destinations":["\xff"]}',
        headers={"Content-Type": "application/json"},
    )
    assert_validation_error(invalid_utf8, "body")

    trip = create_trip(app_client)
    too_many_digits = app_client.patch(
        f"/trips/{trip['id']}",
        content=b'{"version":' + b"9" * 5000 + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert_validation_error(too_many_digits, "body")

    maximum_decodable_integer = app_client.patch(
        f"/trips/{trip['id']}",
        content=b'{"version":' + b"9" * 4300 + b',"budgetUsd":"1.00"}',
        headers={"Content-Type": "application/json"},
    )
    conflict = assert_error(maximum_decodable_integer, 409, "VERSION_CONFLICT")
    assert conflict["details"]["current"] == trip
    assert app_client.get(f"/trips/{trip['id']}").json() == trip

    plain = app_client.post(
        "/trips", content=b"not json", headers={"Content-Type": "text/plain"}
    )
    assert_validation_error(plain, "body")

    empty = app_client.post("/trips", content=b"")
    assert_validation_error(empty, "body")

    non_object = app_client.post("/trips", json=[])
    assert_validation_error(non_object, "body")
