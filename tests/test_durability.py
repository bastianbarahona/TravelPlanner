"""Test 6 of provider-mock/README.md, plus the control interface being off by default.

Each test runs a private mock, because it kills the process or changes configuration.
"""
import time

import httpx

from contract import schema_errors
from support import (
    HTTP_OPTIONS,
    TOKEN_A,
    book,
    booking_body,
    current_offer,
    post_booking,
    provider_client,
    search_body,
)


def restart(mock):
    mock.kill()
    mock.start()
    return provider_client(mock.url, TOKEN_A)


def test_booking_key_and_reference_survive_a_crash(start_mock):
    mock = start_mock()
    with provider_client(mock.url, TOKEN_A) as client:
        key, body, booking = book(client)

    with restart(mock) as client:
        replay = post_booking(client, body, key)
        assert replay.status_code == 200
        assert replay.json() == booking

        assert client.get(f"/bookings/{booking['id']}").json() == booking
        assert client.get(f"/bookings/by-reference/{body['clientReference']}").json() == booking

        conflict = post_booking(client, body)
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "REFERENCE_CONFLICT"


def test_key_conflict_survives_a_crash(start_mock):
    mock = start_mock()
    with provider_client(mock.url, TOKEN_A) as client:
        key, body, _ = book(client)

    with restart(mock) as client:
        response = post_booking(client, body | {"clientReference": "another-reference"}, key)
        assert response.status_code == 409
        assert response.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_pending_booking_resolves_after_a_crash(start_mock):
    mock = start_mock(MOCK_PENDING_DELAY_SECONDS="0.5")
    with provider_client(mock.url, TOKEN_A) as client:
        _, body, booking = book(client, "offer-hotel-002")
        assert booking["status"] == "pending"

    with restart(mock) as client:
        time.sleep(0.6)
        resolved = client.get(f"/bookings/by-reference/{body['clientReference']}").json()
        assert resolved["status"] == "confirmed"
        assert resolved["id"] == booking["id"]


def test_restart_keeps_the_current_quote(start_mock):
    # A13 relies on revisions not being reused, so a restart must not mint or repeat one.
    mock = start_mock()
    with provider_client(mock.url, TOKEN_A) as client:
        before = current_offer(client)

    with restart(mock) as client:
        assert current_offer(client) == before


def test_offer_change_survives_a_restart(start_mock):
    mock = start_mock(MOCK_CONTROL_ENABLED="1")
    new_price = {"amount": "500.00", "currency": "USD"}
    response = httpx.post(f"{mock.url}/__control/offers/offer-hotel-001", json={"price": new_price}, **HTTP_OPTIONS)
    assert response.status_code == 200

    with restart(mock) as client:
        assert current_offer(client)["price"] == new_price


def test_control_interface_is_off_unless_enabled(start_mock):
    mock = start_mock()
    for path in ("reset", "clock", "fault", "offers/offer-hotel-001"):
        response = httpx.post(f"{mock.url}/__control/{path}", json={"status": 503, "now": None}, **HTTP_OPTIONS)
        assert response.status_code == 404
        assert schema_errors("Error", response.json()) == []

    with provider_client(mock.url, TOKEN_A) as client:
        assert client.post("/offers/search", json=search_body()).status_code == 200


def test_rejected_booking_leaves_no_record_after_a_restart(start_mock):
    mock = start_mock()
    with provider_client(mock.url, TOKEN_A) as client:
        body = booking_body(current_offer(client), expectedOfferRevision="quote-old")
        assert post_booking(client, body).status_code == 409

    with restart(mock) as client:
        assert client.get(f"/bookings/by-reference/{body['clientReference']}").status_code == 404
