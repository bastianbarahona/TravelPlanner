"""Test 10 of provider-mock/README.md: bad input always gets an `Error`.

Also steps 1 to 3 of docs/CONTRACT_NOTES.md 2.1.

The client's response hook validates every body here against the `Error` schema,
which rejects FastAPI's default {"detail": ...} because additionalProperties is false.
"""
import json

import httpx
import pytest

from contract import schema_errors
from support import (
    HTTP_OPTIONS,
    TOKEN_A,
    book,
    booking_body,
    check_every_response,
    current_offer,
    new_key,
    post_booking,
    search_body,
)

PRICE = {"amount": "420.00", "currency": "USD"}


def without(body, field):
    return {key: value for key, value in body.items() if key != field}


def assert_bad_request(response):
    assert response.status_code == 400
    assert response.json()["code"] == "BAD_REQUEST"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(without(search_body(), "category"), id="category missing"),
        pytest.param(without(search_body(), "destination"), id="destination missing"),
        pytest.param(without(search_body(), "startsOn"), id="startsOn missing"),
        pytest.param(without(search_body(), "endsOn"), id="endsOn missing"),
        pytest.param(without(search_body(), "travelers"), id="travelers missing"),
        pytest.param(search_body(category="transportation"), id="PRD wording for category"),
        pytest.param(search_body(destination=""), id="empty destination"),
        pytest.param(search_body(destination="x" * 201), id="destination too long"),
        pytest.param(search_body(category="transport"), id="transport without origin"),
        pytest.param(search_body(category="transport", origin=""), id="empty origin"),
        pytest.param(search_body(origin=None), id="null origin"),
        pytest.param(search_body(providerId=None), id="null providerId"),
        pytest.param(search_body(providerId=""), id="empty providerId"),
        pytest.param(search_body(providerId="x" * 129), id="providerId too long"),
        pytest.param(search_body(travelers=0), id="travelers below minimum"),
        pytest.param(search_body(travelers=21), id="travelers above maximum"),
        pytest.param(search_body(travelers="2"), id="travelers as string"),
        pytest.param(search_body(travelers=True), id="travelers as boolean"),
        pytest.param(search_body(startsOn="2030-06-10T00:00:00Z"), id="datetime instead of date"),
        pytest.param(search_body(startsOn="20300610"), id="basic ISO date"),
        pytest.param(search_body(startsOn="2030-02-30"), id="impossible date"),
        pytest.param(search_body(endsOn="2030-06-09"), id="endsOn before startsOn"),
        pytest.param(search_body(extra="field"), id="additional property"),
    ],
)
def test_invalid_search_body_is_400(provider, body):
    assert_bad_request(provider.post("/offers/search", json=body))


def valid_booking(**overrides):
    return {
        "clientReference": "ref-1",
        "offerId": "offer-hotel-001",
        "expectedOfferRevision": "quote-1",
        "expectedPrice": PRICE,
    } | overrides


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(without(valid_booking(), "clientReference"), id="clientReference missing"),
        pytest.param(without(valid_booking(), "offerId"), id="offerId missing"),
        pytest.param(without(valid_booking(), "expectedOfferRevision"), id="revision missing"),
        pytest.param(without(valid_booking(), "expectedPrice"), id="price missing"),
        pytest.param(valid_booking(clientReference=""), id="empty clientReference"),
        pytest.param(valid_booking(clientReference="x" * 129), id="clientReference too long"),
        pytest.param(valid_booking(offerId="x" * 129), id="offerId too long"),
        pytest.param(valid_booking(expectedOfferRevision=""), id="empty revision"),
        pytest.param(valid_booking(expectedOfferRevision=1), id="integer revision"),
        pytest.param(valid_booking(expectedPrice="420.00"), id="bare string price"),
        pytest.param(valid_booking(expectedPrice=PRICE | {"amount": 420.0}), id="numeric amount"),
        pytest.param(valid_booking(expectedPrice=PRICE | {"amount": "420"}), id="no decimals"),
        pytest.param(valid_booking(expectedPrice=PRICE | {"amount": "420.0"}), id="one decimal"),
        pytest.param(valid_booking(expectedPrice=PRICE | {"amount": "01.00"}), id="leading zero"),
        pytest.param(valid_booking(expectedPrice=PRICE | {"amount": "-1.00"}), id="negative amount"),
        pytest.param(valid_booking(expectedPrice=PRICE | {"currency": "usd"}), id="lowercase currency"),
        pytest.param(valid_booking(expectedPrice=PRICE | {"currency": "EUR"}), id="other currency"),
        pytest.param(valid_booking(expectedPrice=PRICE | {"extra": 1}), id="additional price property"),
        pytest.param(valid_booking(extra="field"), id="additional property"),
    ],
)
def test_invalid_booking_body_is_400(provider, body):
    assert_bad_request(post_booking(provider, body))


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"not json", id="not json"),
        pytest.param(b"{", id="truncated"),
        pytest.param(b"[]", id="array"),
        pytest.param(b"null", id="null"),
        pytest.param(b'"text"', id="string"),
        pytest.param(b"\xff\xfe\xff", id="not utf-8"),
        pytest.param(b"[" * 200_000, id="nested too deep"),
    ],
)
@pytest.mark.parametrize("path", ["/offers/search", "/bookings"])
def test_unparseable_body_is_400(provider, path, raw):
    assert_bad_request(provider.post(path, content=raw, headers={"Idempotency-Key": new_key()}))


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"Idempotency-Key": ""}, id="empty"),
        pytest.param({"Idempotency-Key": "x" * 129}, id="too long"),
    ],
)
def test_invalid_idempotency_key_is_400_and_creates_nothing(provider, headers):
    body = booking_body(current_offer(provider))
    assert_bad_request(provider.post("/bookings", json=body, headers=headers))
    assert provider.get(f"/bookings/by-reference/{body['clientReference']}").status_code == 404


def test_idempotency_key_at_the_length_limit_is_accepted(provider):
    response = post_booking(provider, booking_body(current_offer(provider)), "x" * 128)
    assert response.status_code == 201


def test_client_reference_at_the_length_limit_is_accepted(provider):
    body = booking_body(current_offer(provider), clientReference="x" * 128)
    assert post_booking(provider, body).status_code == 201


@pytest.mark.parametrize(
    "path",
    ["/offers/{}", "/bookings/{}", "/bookings/by-reference/{}"],
)
def test_path_value_over_the_limit_is_404_not_422(provider, path):
    # A41: the GET operations declare no 400.
    response = provider.get(path.format("x" * 129))
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "method, path, status",
    [
        pytest.param("GET", "/nope", 404, id="unknown route"),
        pytest.param("GET", "/v1/offers/", 404, id="empty offer id"),
        pytest.param("GET", "/v1/bookings/", 404, id="empty booking id"),
        pytest.param("GET", "/v1/bookings/by-reference/", 404, id="empty reference"),
        pytest.param("POST", "/v1/bookings/", 404, id="trailing slash is not redirected"),
        pytest.param("PUT", "/v1/bookings", 405, id="wrong method"),
        pytest.param("DELETE", "/v1/offers/offer-hotel-001", 405, id="no cancellation"),
    ],
)
def test_undeclared_requests_still_get_an_error_body(unchecked, method, path, status):
    response = unchecked.request(method, path)
    assert response.status_code == status
    assert schema_errors("Error", response.json()) == []


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_framework_generated_docs_are_not_exposed(unchecked, path):
    # A generated /openapi.json would be a second contract, and it would differ.
    assert unchecked.get(path).status_code == 404


def operations(client):
    """One request per contract operation, against records that exist."""
    key, body, booking = book(client)
    return {
        "searchOffers": lambda c: c.post("/offers/search", json=search_body()),
        "getOffer": lambda c: c.get("/offers/offer-hotel-001"),
        "createBooking": lambda c: post_booking(c, body, key),
        "getBookingByReference": lambda c: c.get(f"/bookings/by-reference/{body['clientReference']}"),
        "getBooking": lambda c: c.get(f"/bookings/{booking['id']}"),
    }


OPERATIONS = ["searchOffers", "getOffer", "createBooking", "getBookingByReference", "getBooking"]


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param(None, id="no header"),
        pytest.param("Bearer wrong-token", id="wrong token"),
        pytest.param("Bearer", id="empty token"),
        pytest.param(f"Basic {TOKEN_A}", id="wrong scheme"),
    ],
)
def test_every_operation_refuses_a_bad_token(mock, provider, operation, authorization):
    call = operations(provider)[operation]
    headers = {"Authorization": authorization} if authorization else {}
    with httpx.Client(
        base_url=f"{mock.url}/v1",
        headers=headers,
        event_hooks={"response": [check_every_response]},
        **HTTP_OPTIONS,
    ) as client:
        response = call(client)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_bearer_scheme_is_case_insensitive(mock, provider):
    headers = {"Authorization": f"bearer {TOKEN_A}"}
    assert httpx.get(f"{mock.url}/v1/offers/offer-hotel-001", headers=headers, **HTTP_OPTIONS).status_code == 200


@pytest.mark.parametrize("operation", OPERATIONS)
def test_every_operation_answers_429_with_retry_after(provider, control, operation):
    call = operations(provider)[operation]
    control.fault(429)
    response = call(provider)
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"
    assert int(response.headers["retry-after"]) >= 1


@pytest.mark.parametrize("operation", OPERATIONS)
def test_every_operation_answers_503_during_an_outage(provider, control, operation):
    call = operations(provider)[operation]
    control.fault(503)
    response = call(provider)
    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_every_error_carries_a_distinct_request_id(provider):
    first = provider.get("/offers/nope").json()["requestId"]
    second = provider.get("/offers/nope").json()["requestId"]
    assert first != second


# Steps 1 to 3 of CONTRACT_NOTES.md 2.1.


def test_authentication_wins_over_validation(mock, provider):
    response = httpx.post(f"{mock.url}/v1/offers/search", content=b"not json", **HTTP_OPTIONS)
    assert response.status_code == 401


def test_validation_wins_over_an_outage(provider, control):
    control.fault(503)
    assert_bad_request(provider.post("/offers/search", content=b"not json"))
    assert_bad_request(post_booking(provider, {"bad": "body"}))


@pytest.mark.parametrize("status", [429, 503])
def test_refused_request_creates_no_booking_and_keeps_the_key_free(provider, control, status):
    body, key = booking_body(current_offer(provider)), new_key()
    control.fault(status)
    assert post_booking(provider, body, key).status_code == status

    control.fault(None)
    assert provider.get(f"/bookings/by-reference/{body['clientReference']}").status_code == 404
    assert post_booking(provider, body, key).status_code == 201


def test_error_body_is_json_with_the_declared_fields(provider):
    body = json.loads(provider.get("/offers/nope").content)
    assert set(body) == {"code", "message", "requestId"}
