"""Helpers shared by the Iteration 4 booking suites."""
import time
from copy import deepcopy

import httpx
from sqlalchemy import text

from app_support import StubResponse
from support import HTTP_OPTIONS, TOKEN_A


def add_item(client, trip_id, version, offer_id):
    response = client.post(
        f"/trips/{trip_id}/itinerary/items",
        json={"version": version, "offerId": offer_id},
    )
    assert response.status_code == 201, response.text
    view = response.json()
    return view, next(item for item in view["items"] if item["offer"]["id"] == offer_id)


def booking_terms(item, **changes):
    offer = item["offer"]
    return {
        "expectedRevision": offer["revision"],
        "expectedPriceUsd": offer["price"]["amount"],
    } | changes


def book_item(client, trip_id, item, **changes):
    return client.post(
        f"/trips/{trip_id}/itinerary/items/{item['id']}/booking",
        json=booking_terms(item, **changes),
    )


def retry_item(client, trip_id, item_id):
    return client.post(
        f"/trips/{trip_id}/itinerary/items/{item_id}/booking/retry",
        json={},
    )


def get_booking(client, trip_id, item_id):
    return client.get(f"/trips/{trip_id}/itinerary/items/{item_id}/booking")


def get_itinerary(client, trip_id):
    response = client.get(f"/trips/{trip_id}/itinerary")
    assert response.status_code == 200, response.text
    return response.json()


def delete_item(client, trip_id, item_id, version):
    return client.delete(f"/trips/{trip_id}/itinerary/items/{item_id}?version={version}")


def provider_booking(request, offer, status="confirmed", *, failure=True):
    body = {
        "id": f"booking-{request['body']['clientReference']}",
        "clientReference": request["body"]["clientReference"],
        "offer": deepcopy(offer),
        "status": status,
        "createdAt": "2030-06-01T12:01:00Z",
        "updatedAt": "2030-06-01T12:01:00Z",
    }
    if status == "failed" and failure:
        body["failure"] = {"code": "SUPPLIER_DECLINED", "message": "The supplier declined it."}
    return body


def booking_for_reference(reference, offer, status="confirmed"):
    """The provider's Booking for a reference the stub only knows from a URL."""
    return provider_booking({"body": {"clientReference": reference}}, offer, status)


def latest_attempt(db_engine, item_id):
    """The newest attempt row, or None while the item has none."""
    with db_engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT * FROM booking_attempts WHERE item_id = :item_id "
                "ORDER BY attempt_no DESC LIMIT 1"
            ),
            {"item_id": item_id},
        ).mappings().first()


def attempt_count(db_engine, item_id):
    with db_engine.connect() as connection:
        return connection.scalar(
            text("SELECT count(*) FROM booking_attempts WHERE item_id = :item_id"),
            {"item_id": item_id},
        )


def error_lines(process, needle):
    """ERROR log lines that name the needle."""
    return [line for line in process.read_logs().splitlines() if "ERROR" in line and needle in line]


def posts(stub):
    return [request for request in stub.requests if request["method"] == "POST"]


def reference_reads(stub):
    return [
        request
        for request in stub.requests
        if request["method"] == "GET" and "/bookings/by-reference/" in request["path"]
    ]


def is_offer_read(request):
    return request["method"] == "GET" and "/offers/" in request["path"]


def wait_until(condition, message, timeout=15):
    """Polls a condition against a deadline. Returns its value, or fails with the message."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        assert time.monotonic() < deadline, f"timed out waiting for {message}"
        time.sleep(0.05)


def stays_true(condition, message, window):
    """For a thing that must not happen: the condition has to hold for the whole window."""
    deadline = time.monotonic() + window
    while time.monotonic() < deadline:
        assert condition(), message
        time.sleep(0.05)


def forward(mock_url, request):
    """Answers a stub request by calling the real mock with the same bytes, as a transparent proxy."""
    headers = {"Authorization": f"Bearer {TOKEN_A}", "Content-Type": "application/json"}
    if "idempotency-key" in request["headers"]:
        headers["Idempotency-Key"] = request["headers"]["idempotency-key"]
    response = httpx.request(
        request["method"],
        f"{mock_url}{request['path']}",
        headers=headers,
        content=request["raw_body"] or None,
        **HTTP_OPTIONS,
    )
    reply_headers = {"content-type": "application/json"}
    if "retry-after" in response.headers:
        reply_headers["retry-after"] = response.headers["retry-after"]
    return StubResponse(response.status_code, response.content, reply_headers)
