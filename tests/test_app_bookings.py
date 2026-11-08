"""Iteration 4 booking outcomes, terms, permissions, and itinerary views."""
import re
import threading
from copy import deepcopy

import pytest
from sqlalchemy import text

from app_support import (
    ProviderStub,
    StubResponse,
    add_participant,
    app_client as make_app_client,
    assert_error,
    assert_validation_error,
    create_planning_trip,
    provider_error,
    raw_json_request,
    stub_offer,
)
from booking_support import (
    add_item,
    book_item,
    error_lines,
    get_booking,
    latest_attempt,
    posts,
    provider_booking,
    retry_item,
    wait_until,
)

OUTCOME_CASES = [
    pytest.param("201-confirmed", "confirmed", "confirmed", id="201-confirmed"),
    pytest.param("200-pending", "pending", "pending", id="200-pending"),
    pytest.param("201-failed", "failed", "failed", id="201-failed-without-failure"),
    pytest.param("offer-changed", "rejected", "rejected", id="409-offer-changed"),
    pytest.param("offer-unavailable", "rejected", "rejected", id="409-offer-unavailable"),
    pytest.param("not-found", "rejected", "rejected", id="404"),
    pytest.param("bad-request", "rejected", "unknown", id="400"),
    pytest.param("unauthorized", "unknown", "unknown", id="401"),
    pytest.param("rate-limited", "unknown", "unknown", id="429"),
    pytest.param("unavailable", "unknown", "unknown", id="503"),
    pytest.param("idempotency-adopt", "confirmed", "confirmed", id="idempotency-conflict-adopt"),
    pytest.param("idempotency-missing", "unknown", "unknown", id="idempotency-conflict-missing"),
    pytest.param("reference-adopt", "confirmed", "confirmed", id="reference-conflict-adopt"),
    pytest.param("reference-mismatch-revision", "unknown", "unknown", id="reference-conflict-other-revision"),
    pytest.param("reference-mismatch-offer", "unknown", "unknown", id="reference-conflict-other-offer"),
    pytest.param("timeout", "unknown", "unknown", id="timeout"),
    pytest.param("disconnect", "unknown", "unknown", id="connection-failure"),
    pytest.param("undeclared", "unknown", "unknown", id="undeclared-status"),
    pytest.param("non-json", "unknown", "unknown", id="non-json"),
    pytest.param("schema-invalid", "unknown", "unknown", id="schema-invalid"),
]


# Rows of the 2.8 table that must also reach the operator: our bug, a bad token, or a conflict we could not resolve.
LOGGED_AS_ERROR = {
    "bad-request",
    "unauthorized",
    "idempotency-missing",
    "reference-mismatch-revision",
    "reference-mismatch-offer",
}


@pytest.mark.parametrize("column", ["first", "retry"])
@pytest.mark.parametrize("outcome,first_state,retry_state", OUTCOME_CASES)
def test_every_post_outcome_keeps_identity_and_expected_state(
    start_app, db_engine, column, outcome, first_state, retry_state
):
    """Covers every Iteration 4 section 2.8 row in both columns."""
    offer = stub_offer("matrix-offer")
    held = threading.Event()
    last_post = {}
    post_count = 0

    def selected_reply(request):
        if outcome == "201-confirmed":
            return StubResponse(201, provider_booking(request, offer))
        if outcome == "200-pending":
            return StubResponse(200, provider_booking(request, offer, "pending"))
        if outcome == "201-failed":
            return StubResponse(201, provider_booking(request, offer, "failed", failure=False))
        if outcome == "offer-changed":
            return StubResponse(409, provider_error("OFFER_CHANGED"))
        if outcome == "offer-unavailable":
            return StubResponse(409, provider_error("OFFER_UNAVAILABLE"))
        if outcome == "not-found":
            return StubResponse(404, provider_error("NOT_FOUND"))
        if outcome == "bad-request":
            return StubResponse(400, provider_error("BAD_REQUEST"))
        if outcome == "unauthorized":
            return StubResponse(401, provider_error("UNAUTHORIZED"))
        if outcome == "rate-limited":
            return StubResponse(429, provider_error("RATE_LIMITED"), {"Retry-After": "7"})
        if outcome == "unavailable":
            return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))
        if outcome.startswith("idempotency"):
            return StubResponse(409, provider_error("IDEMPOTENCY_CONFLICT"))
        if outcome.startswith("reference"):
            return StubResponse(409, provider_error("REFERENCE_CONFLICT"))
        if outcome == "timeout":
            held.wait(5)
            return StubResponse(201, provider_booking(request, offer))
        if outcome == "disconnect":
            return StubResponse(disconnect=True)
        if outcome == "undeclared":
            return StubResponse(418, provider_error())
        if outcome == "non-json":
            return StubResponse(201, b"not json", {"Content-Type": "text/plain"})
        assert outcome == "schema-invalid"
        return StubResponse(201, {"status": "confirmed", "extra": True})

    def respond(request):
        nonlocal post_count
        if request["method"] == "GET" and request["path"].startswith("/v1/offers/"):
            return StubResponse(200, offer)
        if request["method"] == "POST":
            post_count += 1
            last_post.clear()
            last_post.update(request)
            if column == "retry" and post_count == 1:
                return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))
            return selected_reply(request)
        assert request["path"].startswith("/v1/bookings/by-reference/")
        if outcome in {"idempotency-adopt", "reference-adopt"}:
            return StubResponse(200, provider_booking(last_post, offer))
        if outcome == "reference-mismatch-revision":  # same offer id, only the revision differs
            return StubResponse(200, provider_booking(last_post, stub_offer(offer["id"], revision="another-revision")))
        if outcome == "reference-mismatch-offer":  # same revision, only the offer id differs
            return StubResponse(200, provider_booking(last_post, stub_offer("another-offer", revision=offer["revision"])))
        return StubResponse(404, provider_error("NOT_FOUND"))

    with ProviderStub(respond) as stub:
        timeout = {"PROVIDER_TIMEOUT_SECONDS": "0.2"} if outcome == "timeout" else {}
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0", **timeout)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            try:
                first = book_item(client, trip["id"], item)
                assert first.status_code == 201, first.text
                if column == "retry":
                    assert first.json()["state"] == "unknown"
                    before = latest_attempt(db_engine, item["id"])
                    response = retry_item(client, trip["id"], item["id"])
                    assert response.status_code == 200, response.text
                else:
                    before = None
                    response = first
            finally:
                held.set()

            expected_state = retry_state if column == "retry" else first_state
            attempt = response.json()
            row = latest_attempt(db_engine, item["id"])
            assert attempt["state"] == row["state"] == expected_state
            assert get_booking(client, trip["id"], item["id"]).json() == attempt
            assert re.fullmatch(r"[0-9a-f]{32}", row["idempotency_key"])
            assert row["client_reference"] == (
                f"trip-{trip['id']}-item-{item['id']}-attempt-1"
            )
            assert len(row["client_reference"]) <= 128
            assert row["request"] == {
                "clientReference": row["client_reference"],
                "offerId": offer["id"],
                "expectedOfferRevision": offer["revision"],
                "expectedPrice": offer["price"],
            }

            posts = [request for request in stub.requests if request["method"] == "POST"]
            assert len(posts) == (2 if column == "retry" else 1)
            if column == "retry":
                assert row["idempotency_key"] == before["idempotency_key"]
                assert row["client_reference"] == before["client_reference"]
                assert posts[0]["headers"]["idempotency-key"] == posts[1]["headers"]["idempotency-key"]
                assert posts[0]["raw_body"] == posts[1]["raw_body"]
            if outcome == "rate-limited":
                assert row["last_error"]["code"] == "RATE_LIMITED"
                assert row["last_error"]["retryAfterSeconds"] == 7
                assert attempt["lastError"]["retryAfterSeconds"] == 7
            if expected_state in {"pending", "confirmed", "failed"}:
                assert attempt["booking"] == row["booking"]
            else:
                assert "booking" not in attempt
            if outcome in LOGGED_AS_ERROR:
                needle = "rejected our token" if outcome == "unauthorized" else row["client_reference"]
                wait_until(lambda: error_lines(process, needle), f"an ERROR log line naming {needle!r}")


@pytest.mark.parametrize(
    "body,field",
    [
        pytest.param({"expectedRevision": "", "expectedPriceUsd": "420.00"}, "expectedRevision", id="empty-revision"),
        pytest.param({"expectedRevision": "revision\x00", "expectedPriceUsd": "420.00"}, "expectedRevision", id="nul-revision"),
        pytest.param({"expectedRevision": "revision", "expectedPriceUsd": "420"}, "expectedPriceUsd", id="bad-money"),
    ],
)
def test_booking_validation_spends_no_attempt_or_provider_call(start_app, db_engine, body, field):
    offer = stub_offer("validation-offer")

    def respond(request):
        if request["method"] == "GET":
            return StubResponse(200, offer)
        return StubResponse(201, provider_booking(request, offer))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            stub.requests.clear()
            response = client.post(
                f"/trips/{trip['id']}/itinerary/items/{item['id']}/booking",
                json=body,
            )
            assert_validation_error(response, field)
        assert not [request for request in stub.requests if request["method"] == "POST"]
        with db_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT count(*) FROM booking_attempts WHERE item_id = :item_id"),
                {"item_id": item["id"]},
            ) == 0


def test_lone_surrogate_revision_is_validation_error_without_provider_call(start_app, db_engine):
    offer = stub_offer("surrogate-offer")

    with ProviderStub(lambda _request: StubResponse(200, offer)) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            stub.requests.clear()
            response = raw_json_request(
                client,
                "POST",
                f"/trips/{trip['id']}/itinerary/items/{item['id']}/booking",
                {"expectedRevision": "revision\ud800", "expectedPriceUsd": "420.00"},
            )
            assert_validation_error(response, "expectedRevision")
        assert stub.requests == []
        with db_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM booking_attempts")) == 0


def test_changed_terms_and_unavailable_snapshot_make_no_provider_post(start_app, db_engine):
    offers = {
        "terms-offer": stub_offer("terms-offer"),
        "unavailable-snapshot": stub_offer("unavailable-snapshot"),
    }

    def respond(request):
        if request["method"] == "GET":
            return StubResponse(200, offers[request["path"].rsplit("/", 1)[1]])
        return StubResponse(201, provider_booking(request, offers[request["body"]["offerId"]]))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            view, terms_item = add_item(client, trip["id"], 1, "terms-offer")
            view, unavailable_item = add_item(client, trip["id"], view["version"], "unavailable-snapshot")

            stub.requests.clear()
            revision = book_item(
                client, trip["id"], terms_item, expectedRevision="different-revision"
            )
            assert_error(revision, 409, "TERMS_CHANGED")
            price = book_item(
                client, trip["id"], terms_item, expectedPriceUsd="421.00"
            )
            assert_error(price, 409, "TERMS_CHANGED")
            assert not [request for request in stub.requests if request["method"] == "POST"]

            offers["unavailable-snapshot"] = stub_offer(
                "unavailable-snapshot", revision="unavailable-v2", available=False
            )
            refreshed = client.post(
                f"/trips/{trip['id']}/itinerary/items/{unavailable_item['id']}/refresh",
                json={"version": view["version"]},
            )
            assert refreshed.status_code == 200
            unavailable_item = next(
                item for item in refreshed.json()["items"] if item["id"] == unavailable_item["id"]
            )
            stub.requests.clear()
            unavailable = book_item(client, trip["id"], unavailable_item)
            assert_error(unavailable, 409, "OFFER_UNAVAILABLE")
            assert not [request for request in stub.requests if request["method"] == "POST"]

        with db_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM booking_attempts")) == 0


def test_provider_quote_change_rejects_without_mutating_stored_snapshot(start_app, control):
    process = start_app(BOOKING_POLL_ENABLED="0")
    with make_app_client(process.url) as client:
        trip = create_planning_trip(client)
        before, item = add_item(client, trip["id"], 1, "offer-hotel-001")
        snapshot = deepcopy(item["offer"])
        control.change_offer(
            "offer-hotel-001", price={"amount": "499.00", "currency": "USD"}
        )
        response = book_item(client, trip["id"], item)
        assert response.status_code == 201
        assert response.json()["state"] == "rejected"
        assert response.json()["lastError"]["code"] == "OFFER_CHANGED"
        after = client.get(f"/trips/{trip['id']}/itinerary").json()
        assert after["version"] == before["version"]
        assert after["items"][0]["offer"] == snapshot
        assert after["budget"]["lines"][0]["amountUsd"] == snapshot["price"]["amount"]


@pytest.mark.parametrize("first_state", ["rejected", "failed", "confirmed"])
def test_closed_attempt_controls_new_attempt_and_terminal_retry(start_app, db_engine, first_state):
    offer = stub_offer(f"{first_state}-offer")
    posts = 0

    def respond(request):
        nonlocal posts
        if request["method"] == "GET":
            return StubResponse(200, offer)
        posts += 1
        if posts == 1 and first_state == "rejected":
            return StubResponse(409, provider_error("OFFER_CHANGED"))
        if posts == 1 and first_state == "failed":
            return StubResponse(201, provider_booking(request, offer, "failed"))
        return StubResponse(201, provider_booking(request, offer))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            first = book_item(client, trip["id"], item)
            assert first.status_code == 201
            assert first.json()["state"] == first_state
            terminal_retry = retry_item(client, trip["id"], item["id"])
            assert_error(terminal_retry, 409, "NOTHING_TO_RETRY")

            second = book_item(client, trip["id"], item)
            if first_state == "confirmed":
                error = assert_error(second, 409, "ALREADY_BOOKED")
                assert error["details"]["current"] == first.json()
                assert posts == 1
            else:
                assert second.status_code == 201
                assert second.json()["state"] == "confirmed"
                assert second.json()["attempt"] == 2
                assert posts == 2
                with db_engine.connect() as connection:
                    rows = connection.execute(
                        text(
                            "SELECT attempt_no, idempotency_key, client_reference FROM booking_attempts "
                            "WHERE item_id = :item_id ORDER BY attempt_no"
                        ),
                        {"item_id": item["id"]},
                    ).mappings().all()
                assert [row["attempt_no"] for row in rows] == [1, 2]
                assert rows[0]["idempotency_key"] != rows[1]["idempotency_key"]
                assert rows[0]["client_reference"].endswith("-attempt-1")
                assert rows[1]["client_reference"].endswith("-attempt-2")


def test_same_offer_cannot_be_added_twice(start_app):
    offer = stub_offer("one-item-offer")
    with ProviderStub(lambda _request: StubResponse(200, offer)) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            current, _ = add_item(client, trip["id"], 1, offer["id"])
            duplicate = client.post(
                f"/trips/{trip['id']}/itinerary/items",
                json={"version": current["version"], "offerId": offer["id"]},
            )
            error = assert_error(duplicate, 409, "OFFER_ALREADY_IN_ITINERARY")
            assert error["details"]["current"] == current
            assert len(client.get(f"/trips/{trip['id']}/itinerary").json()["items"]) == 1


def test_confirmed_budget_uses_accepted_price_and_mixed_outcomes_remain_visible(
    start_app, control
):
    process = start_app(BOOKING_POLL_ENABLED="0")
    with make_app_client(process.url) as client:
        trip = create_planning_trip(client, budgetUsd="1000.00")
        view, confirmed_item = add_item(client, trip["id"], 1, "offer-hotel-001")
        view, failed_item = add_item(client, trip["id"], view["version"], "offer-hotel-004")
        confirmed = book_item(client, trip["id"], confirmed_item)
        failed = book_item(client, trip["id"], failed_item)
        assert confirmed.json()["state"] == "confirmed"
        assert failed.json()["state"] == "failed"

        accepted_amount = confirmed.json()["booking"]["offer"]["price"]["amount"]
        before_change = client.get(f"/trips/{trip['id']}/itinerary").json()
        states = {item["offer"]["id"]: item["booking"]["state"] for item in before_change["items"]}
        assert states == {"offer-hotel-001": "confirmed", "offer-hotel-004": "failed"}
        confirmed_line = next(
            line for line in before_change["budget"]["lines"] if line["itemId"] == confirmed_item["id"]
        )
        assert confirmed_line["priceKind"] == "confirmed"
        assert confirmed_line["amountUsd"] == accepted_amount
        confirmed_view = next(item for item in before_change["items"] if item["id"] == confirmed_item["id"])
        assert confirmed_view["bookable"] is False

        control.change_offer(
            "offer-hotel-001", price={"amount": "777.00", "currency": "USD"}
        )
        refreshed = client.post(
            f"/trips/{trip['id']}/itinerary/items/{confirmed_item['id']}/refresh",
            json={"version": before_change["version"]},
        )
        assert refreshed.status_code == 200
        after_change = refreshed.json()
        changed_item = next(item for item in after_change["items"] if item["id"] == confirmed_item["id"])
        changed_line = next(
            line for line in after_change["budget"]["lines"] if line["itemId"] == confirmed_item["id"]
        )
        assert changed_item["offer"]["price"]["amount"] == "777.00"
        assert changed_item["booking"]["booking"]["offer"]["price"]["amount"] == accepted_amount
        assert changed_line["priceKind"] == "confirmed"
        assert changed_line["amountUsd"] == accepted_amount


def test_booking_permissions_and_viewer_state_read(start_app):
    offer = stub_offer("permission-offer")

    def respond(request):
        if request["method"] == "GET":
            return StubResponse(200, offer)
        return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with (
            make_app_client(process.url) as organizer,
            make_app_client(process.url, "ben") as viewer,
            make_app_client(process.url, "dee") as outsider,
        ):
            trip = create_planning_trip(organizer)
            add_participant(organizer, trip["id"], "ben", "viewer")
            _, item = add_item(organizer, trip["id"], 1, offer["id"])

            assert_error(book_item(viewer, trip["id"], item), 403, "FORBIDDEN")
            assert_error(book_item(outsider, trip["id"], item), 404, "NOT_FOUND")
            created = book_item(organizer, trip["id"], item)
            assert created.status_code == 201
            assert created.json()["state"] == "unknown"
            assert get_booking(viewer, trip["id"], item["id"]).json() == created.json()
            assert_error(retry_item(viewer, trip["id"], item["id"]), 403, "FORBIDDEN")
            assert_error(retry_item(outsider, trip["id"], item["id"]), 404, "NOT_FOUND")
            assert len([request for request in stub.requests if request["method"] == "POST"]) == 1


def test_retry_of_a_pending_attempt_is_nothing_to_retry_and_sends_no_post(start_app):
    offer = stub_offer("pending-retry-offer")

    def respond(request):
        if request["method"] == "GET":
            return StubResponse(200, offer)
        return StubResponse(201, provider_booking(request, offer, "pending"))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            first = book_item(client, trip["id"], item)
            assert first.json()["state"] == "pending"
            error = assert_error(retry_item(client, trip["id"], item["id"]), 409, "NOTHING_TO_RETRY")
            assert error["details"]["current"] == first.json()
            assert_error(book_item(client, trip["id"], item), 409, "BOOKING_IN_PROGRESS")
            assert len(posts(stub)) == 1


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="absent"),
        pytest.param({"Retry-After": "soon"}, id="not-a-number"),
        pytest.param({"Retry-After": "0"}, id="below-the-minimum"),
        pytest.param({"Retry-After": "Wed, 21 Oct 2037 07:28:00 GMT"}, id="http-date"),
    ],
)
def test_rate_limit_without_a_valid_retry_after_omits_retry_after_seconds(start_app, db_engine, headers):
    offer = stub_offer("retry-after-offer")

    def respond(request):
        if request["method"] == "GET":
            return StubResponse(200, offer)
        return StubResponse(429, provider_error("RATE_LIMITED"), headers)

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            view = book_item(client, trip["id"], item).json()
            assert view["state"] == "unknown"
            assert view["lastError"]["code"] == "RATE_LIMITED"
            assert "retryAfterSeconds" not in view["lastError"]
            assert "retryAfterSeconds" not in latest_attempt(db_engine, item["id"])["last_error"]


def test_retry_with_a_null_body_is_a_validation_error_but_no_body_and_an_empty_object_work(start_app):
    offer = stub_offer("null-body-offer")

    def respond(request):
        if request["method"] == "GET":
            return StubResponse(200, offer)
        return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            assert book_item(client, trip["id"], item).json()["state"] == "unknown"
            route = f"/trips/{trip['id']}/itinerary/items/{item['id']}/booking/retry"
            assert len(posts(stub)) == 1

            for content in (b"null",):
                response = client.post(route, content=content, headers={"Content-Type": "application/json"})
                assert_error(response, 422, "VALIDATION_FAILED")
                assert len(posts(stub)) == 1, content  # nothing was sent to the provider

            assert client.post(route).status_code == 200  # no body at all
            assert client.post(route, json={}).status_code == 200
            assert len(posts(stub)) == 3
