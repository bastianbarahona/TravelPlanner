"""The checkers run on every response, so they must be proven able to fail."""
import hashlib
import json

import pytest

from contract import CONTRACT_PATH, check_response

HEADERS = {"content-type": "application/json"}

OFFER = {
    "id": "offer-1",
    "providerId": "supplier-a",
    "category": "accommodation",
    "title": "Title",
    "description": "Description",
    "destination": "Lisbon",
    "startsAt": "2030-06-10T15:00:00+01:00",
    "endsAt": "2030-06-13T11:00:00+01:00",
    "travelers": 2,
    "price": {"amount": "420.00", "currency": "USD"},
    "revision": "quote-1",
    "available": True,
    "validUntil": "2030-06-01T12:15:00Z",
}
BOOKING = {
    "id": "booking-1",
    "clientReference": "ref-1",
    "offer": OFFER,
    "status": "confirmed",
    "createdAt": "2030-06-01T12:01:00Z",
    "updatedAt": "2030-06-01T12:01:00Z",
}
SEARCH = {"offers": [OFFER], "partial": False, "warnings": [], "searchedAt": "2030-06-01T12:00:00Z"}
WARNING = {"providerId": "supplier-b", "code": "PROVIDER_TIMEOUT", "message": "Timed out."}
ERROR = {"code": "RATE_LIMITED", "message": "Slow down.", "requestId": "request-1"}


def check_offer(offer):
    check_response("GET", "/offers/offer-1", 200, HEADERS, json.dumps(offer))


def check_booking(booking):
    check_response("GET", "/bookings/booking-1", 200, HEADERS, json.dumps(booking))


def check_search(result):
    check_response("POST", "/offers/search", 200, HEADERS, json.dumps(result))


def test_valid_examples_pass():
    check_offer(OFFER)
    check_booking(BOOKING)
    check_search(SEARCH)
    check_search(SEARCH | {"partial": True, "warnings": [WARNING]})


@pytest.mark.parametrize(
    "bad_offer",
    [
        pytest.param(OFFER | {"validUntil": "not-a-date"}, id="malformed date-time needs rfc3339-validator"),
        pytest.param(OFFER | {"validUntil": "2030-06-01T12:15:00"}, id="date-time without offset"),
        pytest.param(OFFER | {"revision": 1}, id="integer revision"),
        pytest.param(OFFER | {"price": {"amount": "420", "currency": "USD"}}, id="amount without decimals"),
        pytest.param(OFFER | {"price": {"amount": 420.0, "currency": "USD"}}, id="numeric amount"),
        pytest.param(OFFER | {"origin": None}, id="explicit null"),
        pytest.param(OFFER | {"extra": "field"}, id="additional property"),
        pytest.param(OFFER | {"endsAt": OFFER["startsAt"]}, id="prose: endsAt equal to startsAt"),
        pytest.param(OFFER | {"category": "transport"}, id="prose: transport without origin"),
    ],
)
def test_offer_violations_are_detected(bad_offer):
    with pytest.raises(AssertionError):
        check_offer(bad_offer)


@pytest.mark.parametrize(
    "bad_booking",
    [
        pytest.param(BOOKING | {"status": "unknown"}, id="unknown is never a provider status"),
        pytest.param(BOOKING | {"status": "failed"}, id="prose: failed without failure"),
        pytest.param(BOOKING | {"failure": {"code": "X", "message": "Y"}}, id="prose: failure on confirmed"),
        pytest.param(BOOKING | {"failure": None}, id="explicit null failure"),
        pytest.param(BOOKING | {"updatedAt": "2030-06-01T12:00:59Z"}, id="prose: updatedAt before createdAt"),
        pytest.param(
            BOOKING | {"updatedAt": "2030-06-01T13:00:00+02:00"}, id="prose: offsets compared as instants"
        ),
    ],
)
def test_booking_violations_are_detected(bad_booking):
    with pytest.raises(AssertionError):
        check_booking(bad_booking)


@pytest.mark.parametrize(
    "bad_result",
    [
        pytest.param(SEARCH | {"partial": True}, id="prose: partial without warnings"),
        pytest.param(SEARCH | {"warnings": [WARNING]}, id="prose: warnings without partial"),
        pytest.param({k: v for k, v in SEARCH.items() if k != "warnings"}, id="warnings is required"),
    ],
)
def test_search_violations_are_detected(bad_result):
    with pytest.raises(AssertionError):
        check_search(bad_result)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(HEADERS, id="missing"),
        pytest.param(HEADERS | {"retry-after": "0"}, id="zero"),
        pytest.param(HEADERS | {"retry-after": "Wed, 21 Oct 2030 07:28:00 GMT"}, id="http date"),
    ],
)
def test_bad_retry_after_is_detected(headers):
    with pytest.raises(AssertionError):
        check_response("GET", "/offers/offer-1", 429, headers, json.dumps(ERROR))


def test_undeclared_status_is_detected():
    with pytest.raises(AssertionError):
        check_response("GET", "/offers/offer-1", 422, HEADERS, json.dumps({"detail": []}))


def test_fastapi_default_error_body_is_detected():
    with pytest.raises(AssertionError):
        check_response("POST", "/offers/search", 400, HEADERS, json.dumps({"detail": "Bad request"}))


def test_contract_file_is_byte_identical_to_the_supplied_one():
    assert hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest() == (
        "9bf9b93e1083a76a373019562e5f3880466fe349b2afc5d1ea1c12f74c291be9"
    )
