"""Iteration 4 required test 10: request ids, the provider-call log line and log hygiene."""
import re
import threading
from uuid import uuid4

import httpx
import pytest

from app_support import (
    REQUEST_ID,
    ProviderStub,
    StubResponse,
    app_client as make_app_client,
    assert_error,
    create_planning_trip,
    provider_error,
    stub_offer,
)
from booking_support import add_item, book_item, is_offer_read, retry_item, wait_until
from support import HTTP_OPTIONS

CALL_LINE = re.compile(
    r"provider call op=(?P<op>\w+) status=(?P<status>\w+) "
    r"requestId=(?P<request_id>\S+) providerRequestId=(?P<provider_id>\S+)"
)


def provider_calls(process, op):
    return [match.groupdict() for match in CALL_LINE.finditer(process.read_logs()) if match["op"] == op]


def test_every_success_carries_a_fresh_request_id_and_errors_echo_it(application, app_client):
    trip = create_planning_trip(app_client)
    ids = []
    for path in ("/health", f"/trips/{trip['id']}"):
        response = app_client.get(path)
        assert response.status_code == 200
        assert REQUEST_ID.fullmatch(response.headers["x-request-id"]), response.headers
        ids.append(response.headers["x-request-id"])
    ids.append(httpx.get(f"{application.url}/health", **HTTP_OPTIONS).headers["x-request-id"])
    assert len(set(ids)) == 3

    missing = app_client.get(f"/trips/{uuid4()}")
    body = assert_error(missing, 404, "NOT_FOUND")
    assert REQUEST_ID.fullmatch(body["requestId"])
    assert body["requestId"] == missing.headers["x-request-id"]


# (what the provider does to the booking POST, the status token the log line must carry, the provider id it must show)
PROVIDER_CALLS = [
    pytest.param("error", "409", "stub-request-", id="provider-error-with-its-request-id"),
    pytest.param("timeout", "timeout", "-", id="timeout"),
    pytest.param("disconnect", "unreachable", "-", id="connection-failure"),
    pytest.param("non-json", "invalid", "-", id="invalid-body"),
]


@pytest.mark.parametrize("mode,status,provider_id", PROVIDER_CALLS)
def test_each_provider_call_logs_exactly_one_line_with_both_request_ids(
    start_app, mode, status, provider_id
):
    offer = stub_offer("diagnostics-offer")
    provider_request_id = f"stub-request-{uuid4().hex}"
    release = threading.Event()

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if mode == "error":
            error = provider_error("OFFER_CHANGED") | {"requestId": provider_request_id}
            return StubResponse(409, error)
        if mode == "timeout":
            release.wait(5)
            return StubResponse(disconnect=True)
        if mode == "disconnect":
            return StubResponse(disconnect=True)
        return StubResponse(201, b"not json", {"Content-Type": "text/plain"})

    with ProviderStub(respond) as stub:
        timeout = {"PROVIDER_TIMEOUT_SECONDS": "0.2"} if mode == "timeout" else {}
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0", **timeout)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            try:
                response = book_item(client, trip["id"], item)
            finally:
                release.set()
            assert response.status_code == 201, response.text
            our_id = response.headers["x-request-id"]
            assert REQUEST_ID.fullmatch(our_id)

        calls = wait_until(lambda: provider_calls(process, "createBooking"), "the createBooking log line")
        assert len(calls) == 1, calls
        assert calls[0]["status"] == status
        assert calls[0]["request_id"] == our_id
        if mode == "error":
            assert calls[0]["provider_id"] == provider_request_id
        else:
            assert calls[0]["provider_id"] == "-"
        reads = provider_calls(process, "getOffer")
        assert len(reads) == 1 and reads[0]["status"] == "200" and reads[0]["provider_id"] == "-"
        assert reads[0]["request_id"] != our_id


def test_a_provider_failure_on_add_logs_the_error_body_request_id_the_client_sees(start_app):
    provider_request_id = f"stub-request-{uuid4().hex}"
    error = provider_error("PROVIDER_UNAVAILABLE") | {"requestId": provider_request_id}
    with ProviderStub(lambda request: StubResponse(503, error)) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            response = client.post(
                f"/trips/{trip['id']}/itinerary/items", json={"version": 1, "offerId": "any-offer"}
            )
            assert response.status_code >= 400
            body = response.json()
            assert body["requestId"] == response.headers["x-request-id"]
        calls = wait_until(lambda: provider_calls(process, "getOffer"), "the getOffer log line")
        assert calls
        assert all(call["request_id"] == body["requestId"] for call in calls)
        assert all(call["status"] == "503" and call["provider_id"] == provider_request_id for call in calls)


def test_the_background_loop_logs_its_own_poll_request_id(start_app):
    offer = stub_offer("poll-id-offer")
    provider_request_id = f"stub-request-{uuid4().hex}"

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if request["method"] == "POST":
            return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))
        return StubResponse(404, provider_error("NOT_FOUND") | {"requestId": provider_request_id})

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_INTERVALS="0.2,0.2,0.5")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            booked = book_item(client, trip["id"], item)
            user_id = booked.headers["x-request-id"]
            reference = booked.json()["clientReference"]
            retry_id = retry_item(client, trip["id"], item["id"]).headers["x-request-id"]
        calls = wait_until(
            lambda: provider_calls(process, "getBookingByReference"), "a poller log line"
        )
        assert all(re.fullmatch(r"poll-[0-9a-f]{32}", call["request_id"]) for call in calls), calls
        logs = process.read_logs()
        # The poll's own line and its provider-call line share one request id.
        polls = set(re.findall(rf"reconciling {re.escape(reference)} requestId=(poll-[0-9a-f]{{32}})", logs))
        assert polls and {call["request_id"] for call in calls} <= polls, (polls, calls)
        # And the book action's line shares the id of the response and of the createBooking call line.
        booking_ids = re.findall(rf"\bbooking {re.escape(reference)} requestId=(\S+)", logs)
        assert booking_ids == [user_id, retry_id]  # book, then retry: one line each
        assert [call["request_id"] for call in provider_calls(process, "createBooking")] == [user_id, retry_id]
        assert all(call["status"] == "404" and call["provider_id"] == provider_request_id for call in calls)
        assert user_id not in {call["request_id"] for call in calls}
        assert re.fullmatch(r"request-[0-9a-f]{32}", provider_calls(process, "createBooking")[0]["request_id"])


def test_logs_never_contain_the_provider_token(start_app):
    offer = stub_offer("secret-offer")
    with ProviderStub(
        lambda request: StubResponse(200, offer) if is_offer_read(request) else StubResponse(503, provider_error())
    ) as stub:
        process = start_app(
            PROVIDER_BASE_URL=stub.url, PROVIDER_TOKEN="very-secret-provider-token", BOOKING_POLL_ENABLED="0"
        )
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            book_item(client, trip["id"], item)
        wait_until(lambda: provider_calls(process, "createBooking"), "the createBooking log line")
        logs = process.read_logs()
        assert "very-secret-provider-token" not in logs
