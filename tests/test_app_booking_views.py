"""Iteration 4 API shape: attempt view, error details and strict parsing of provider bookings."""
from uuid import uuid4

import pytest

from app_support import (
    ProviderStub,
    StubResponse,
    app_client as make_app_client,
    assert_error,
    create_planning_trip,
    provider_error,
    stub_offer,
)
from booking_support import (
    add_item,
    book_item,
    get_booking,
    get_itinerary,
    is_offer_read,
    latest_attempt,
    posts,
    provider_booking,
    retry_item,
)

REQUIRED_KEYS = {
    "id", "itemId", "attempt", "state", "clientReference", "requested", "requestedBy", "createdAt", "nextStep",
}
OPTIONAL_KEYS = {"booking", "lastError", "lastCheckedAt"}


def assert_no_nulls(value):
    if isinstance(value, dict):
        for key, child in value.items():
            assert child is not None, f"{key} is null"
            assert_no_nulls(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_nulls(child)


def test_state_endpoint_is_404_before_any_attempt_and_a_second_click_names_the_open_attempt(start_app):
    offer = stub_offer("progress-offer")

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            assert "booking" not in item
            assert_error(get_booking(client, trip["id"], item["id"]), 404, "NOT_FOUND")

            first = book_item(client, trip["id"], item)
            assert first.json()["state"] == "unknown"
            second = book_item(client, trip["id"], item)
            error = assert_error(second, 409, "BOOKING_IN_PROGRESS")
            assert error["details"]["current"] == first.json()
            assert len(posts(stub)) == 1
            assert get_itinerary(client, trip["id"])["items"][0]["booking"] == first.json()


def test_terms_changed_carries_the_item_exactly_as_the_itinerary_shows_it(start_app):
    offer = stub_offer("terms-offer")
    with ProviderStub(lambda request: StubResponse(200, offer)) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            stub.requests.clear()
            response = book_item(client, trip["id"], item, expectedRevision="older-revision")
            error = assert_error(response, 409, "TERMS_CHANGED")
            assert error["details"]["current"] == get_itinerary(client, trip["id"])["items"][0]
            assert error["details"]["current"] == item
            assert stub.requests == []


# provider answer to the first POST -> (state, lastError code, words the next step must contain)
VIEW_CASES = {
    "unknown": (lambda request, offer: StubResponse(503, provider_error("PROVIDER_UNAVAILABLE")), "unknown", "PROVIDER_UNAVAILABLE", "not known yet"),
    "pending": (lambda request, offer: StubResponse(201, provider_booking(request, offer, "pending")), "pending", None, "processing"),
    "confirmed": (lambda request, offer: StubResponse(201, provider_booking(request, offer)), "confirmed", None, "booked"),
    "failed": (lambda request, offer: StubResponse(201, provider_booking(request, offer, "failed")), "failed", None, "The supplier declined it."),
    "unauthorized": (lambda request, offer: StubResponse(401, provider_error("UNAUTHORIZED")), "unknown", "UNAUTHORIZED", ""),
    "changed": (lambda request, offer: StubResponse(409, provider_error("OFFER_CHANGED")), "rejected", "OFFER_CHANGED", "refresh"),
    "unavailable": (lambda request, offer: StubResponse(409, provider_error("OFFER_UNAVAILABLE")), "rejected", "OFFER_UNAVAILABLE", "search"),
    "not-found": (lambda request, offer: StubResponse(404, provider_error("NOT_FOUND")), "rejected", "NOT_FOUND", "search"),
    "invalid": (lambda request, offer: StubResponse(201, b"not json", {"Content-Type": "text/plain"}), "unknown", "PROVIDER_INVALID_RESPONSE", ""),
    "unreachable": (lambda request, offer: StubResponse(disconnect=True), "unknown", "PROVIDER_UNREACHABLE", ""),
}


@pytest.mark.parametrize("case", VIEW_CASES)
def test_attempt_view_omits_instead_of_null_and_explains_the_next_step(start_app, case):
    answer, state, error_code, words = VIEW_CASES[case]
    offer = stub_offer(f"view-{case}-offer")

    def respond(request):
        return StubResponse(200, offer) if is_offer_read(request) else answer(request, offer)

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            created = book_item(client, trip["id"], item)
            assert created.status_code == 201, created.text
            view = created.json()

            assert REQUIRED_KEYS <= view.keys() <= REQUIRED_KEYS | OPTIONAL_KEYS
            assert_no_nulls(view)
            assert view["state"] == state
            assert view["itemId"] == item["id"] and view["attempt"] == 1
            assert view["requestedBy"] == "ana"
            assert view["requested"] == {
                "offerId": offer["id"],
                "expectedOfferRevision": offer["revision"],
                "expectedPriceUsd": offer["price"]["amount"],
            }
            assert isinstance(view["nextStep"], str) and view["nextStep"]
            assert words.lower() in view["nextStep"].lower()
            if state in {"unknown", "pending", "confirmed"}:
                assert get_itinerary(client, trip["id"])["items"][0]["bookable"] is False
            assert ("booking" in view) == (state in {"pending", "confirmed", "failed"})
            if error_code is None:
                assert "lastError" not in view
            else:
                assert view["lastError"]["code"] == error_code
                assert view["lastError"]["message"]
            if state == "failed":
                assert view["booking"]["failure"]["message"] in view["nextStep"]
            assert get_booking(client, trip["id"], item["id"]).json() == view
            assert get_itinerary(client, trip["id"])["items"][0]["booking"] == view


def test_last_error_is_cleared_when_a_retry_adopts_a_booking(start_app):
    offer = stub_offer("clear-offer")

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if len(posts(stub)) == 1:
            return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))
        return StubResponse(201, provider_booking(request, offer))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            assert "lastError" in book_item(client, trip["id"], item).json()
            retried = retry_item(client, trip["id"], item["id"]).json()
            assert retried["state"] == "confirmed" and "lastError" not in retried


@pytest.mark.parametrize("flaw", ["wrong-reference", "invalid-amount", "newline-amount", "provider-says-unknown"])
def test_a_booking_that_breaks_the_contract_or_our_reference_leaves_the_attempt_unknown(
    start_app, db_engine, flaw
):
    offer = stub_offer("flaw-offer")

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        body = provider_booking(request, offer)
        if flaw == "wrong-reference":
            body["clientReference"] = "somebody-elses-reference"
        elif flaw == "invalid-amount":
            body["offer"]["price"]["amount"] = "420"
        elif flaw == "newline-amount":  # the contract's `$` lets this through, app/money.py does not
            body["offer"]["price"]["amount"] = "420.00\n"
        else:
            body["status"] = "unknown"
        return StubResponse(201, body)

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            view = book_item(client, trip["id"], item).json()
            assert view["state"] == "unknown"
            assert "booking" not in view
            assert latest_attempt(db_engine, item["id"])["booking"] is None
            assert latest_attempt(db_engine, item["id"])["client_reference"] == view["clientReference"]


def test_malformed_missing_and_other_trip_item_ids_are_the_same_404_on_all_three_routes(start_app):
    with ProviderStub(lambda request: StubResponse(200, stub_offer(request["path"].rsplit("/", 1)[1]))) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            other = create_planning_trip(client)
            _, other_item = add_item(client, other["id"], 1, "other-trips-offer")
            terms = {"expectedRevision": "stub-revision-1", "expectedPriceUsd": "420.00"}
            for item_id in ("not-a-uuid", str(uuid4()), other_item["id"]):
                base = f"/trips/{trip['id']}/itinerary/items/{item_id}/booking"
                assert_error(client.post(base, json=terms), 404, "NOT_FOUND")
                assert_error(client.post(f"{base}/retry", json={}), 404, "NOT_FOUND")
                assert_error(client.get(base), 404, "NOT_FOUND")


@pytest.mark.parametrize("first", ["unknown", "confirmed"])
def test_an_open_or_confirmed_attempt_is_refused_before_the_terms_are_compared(start_app, first):
    offer = stub_offer(f"order-{first}-offer")

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if first == "unknown":
            return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))
        return StubResponse(201, provider_booking(request, offer))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            assert book_item(client, trip["id"], item).json()["state"] == first
            again = book_item(client, trip["id"], item, expectedRevision="older-revision")
            assert_error(again, 409, "BOOKING_IN_PROGRESS" if first == "unknown" else "ALREADY_BOOKED")
            assert len(posts(stub)) == 1


def test_a_confirmed_item_raises_no_expired_or_unavailable_issue_but_an_estimate_does(start_app, control):
    process = start_app(BOOKING_POLL_ENABLED="0")
    with make_app_client(process.url) as client:
        trip = create_planning_trip(client)
        view, booked = add_item(client, trip["id"], 1, "offer-hotel-001")
        view, estimate = add_item(client, trip["id"], view["version"], "offer-activity-001")
        assert book_item(client, trip["id"], booked).json()["state"] == "confirmed"

        try:
            control.pin_clock("2019-01-01T00:00:00Z")
            version = view["version"]
            for offer_id, item in (("offer-hotel-001", booked), ("offer-activity-001", estimate)):
                control.change_offer(offer_id, available=False, validUntil="2020-01-01T00:00:00Z")
                refreshed = client.post(
                    f"/trips/{trip['id']}/itinerary/items/{item['id']}/refresh", json={"version": version}
                )
                assert refreshed.status_code == 200, refreshed.text
                version = refreshed.json()["version"]

            issues = get_itinerary(client, trip["id"])["issues"]
            for code in ("PRICE_EXPIRED", "UNAVAILABLE"):
                raised = [item_id for issue in issues if issue["code"] == code for item_id in issue["itemIds"]]
                assert estimate["id"] in raised, (code, issues)  # the control: an estimate does raise it
                assert booked["id"] not in raised, (code, issues)
        finally:
            control.reset()
