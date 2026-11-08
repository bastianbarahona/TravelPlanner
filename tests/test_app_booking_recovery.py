"""Iteration 4 required tests 2 and 3: crash after commit, and background reconciliation."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import text

from app_support import (
    ProviderStub,
    StubResponse,
    app_client as make_app_client,
    create_planning_trip,
    provider_error,
    stub_offer,
)
from booking_support import (
    add_item,
    book_item,
    booking_for_reference,
    forward,
    get_booking,
    is_offer_read,
    latest_attempt,
    posts,
    provider_booking,
    reference_reads,
    retry_item,
    stays_true,
    wait_until,
)

FAST_POLLING = {"BOOKING_POLL_INTERVALS": "0.2,0.2,0.5"}


def assert_committed_unknown(row, item_id, trip_id):
    """The row a crash would leave behind: committed before the provider answered."""
    assert row["state"] == "unknown"
    assert row["booking"] is None
    assert row["idempotency_key"] and row["client_reference"]
    assert row["client_reference"] == f"trip-{trip_id}-item-{item_id}-attempt-1"
    assert row["attempt_no"] == 1
    assert row["requested_by"] == "ana"
    assert row["request"]["clientReference"] == row["client_reference"]


def test_crash_while_post_is_unanswered_keeps_unknown_and_retry_resends_same_key_and_body(
    start_app, db_engine
):
    offer = stub_offer("crash-a-offer")
    release = threading.Event()

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if request["method"] == "POST":
            if len(posts(stub)) == 1:
                release.wait(30)
                return StubResponse(disconnect=True)
            return StubResponse(201, provider_booking(request, offer))
        return StubResponse(404, provider_error("NOT_FOUND"))

    with ProviderStub(respond) as stub:
        process = start_app(
            PROVIDER_BASE_URL=stub.url,
            PROVIDER_TIMEOUT_SECONDS="60",
            BOOKING_POLL_ENABLED="0",
            **FAST_POLLING,
        )
        try:
            with make_app_client(process.url) as client, ThreadPoolExecutor(1) as pool:
                trip = create_planning_trip(client)
                _, item = add_item(client, trip["id"], 1, offer["id"])
                held = pool.submit(book_item, client, trip["id"], item)
                wait_until(lambda: len(posts(stub)) == 1, "the POST to reach the provider")

                before = latest_attempt(db_engine, item["id"])
                assert_committed_unknown(before, item["id"], trip["id"])
                assert not held.done()

                process.kill()
                release.set()
                with pytest.raises(httpx.TransportError):
                    held.result(timeout=10)
        finally:
            release.set()

        process.env["BOOKING_POLL_ENABLED"] = "1"
        process.start()
        with make_app_client(process.url) as client:
            assert get_booking(client, trip["id"], item["id"]).json()["state"] == "unknown"
            wait_until(
                lambda: latest_attempt(db_engine, item["id"])["check_count"] >= 2,
                "the restarted poller to check twice",
            )
            wait_until(lambda: len(reference_reads(stub)) >= 2, "two by-reference reads to arrive")
            assert reference_reads(stub)[0]["path"].endswith("/" + before["client_reference"])
            view = get_booking(client, trip["id"], item["id"]).json()
            assert view["state"] == "unknown"
            assert "booking" not in view
            assert len(posts(stub)) == 1  # the loop reads and never POSTs

            retried = retry_item(client, trip["id"], item["id"])
            assert retried.status_code == 200, retried.text
            assert retried.json()["state"] == "confirmed"

        sent = posts(stub)
        assert len(sent) == 2
        assert sent[0]["headers"]["idempotency-key"] == sent[1]["headers"]["idempotency-key"]
        assert sent[0]["headers"]["idempotency-key"] == before["idempotency_key"]
        assert sent[0]["raw_body"] == sent[1]["raw_body"]
        assert json.loads(sent[1]["raw_body"]) == before["request"]
        after = latest_attempt(db_engine, item["id"])
        assert after["state"] == "confirmed"
        assert after["idempotency_key"] == before["idempotency_key"]
        assert after["client_reference"] == before["client_reference"]


def test_crash_after_provider_accepted_reconciles_to_confirmed_without_a_second_post(
    start_app, db_engine, mock, provider
):
    release = threading.Event()
    accepted = {}

    def lose_the_answer(request):
        reply = forward(mock.url, request)
        if request["method"] != "POST":
            return reply
        accepted["status"] = reply.status
        release.wait(30)
        return StubResponse(disconnect=True)

    with (
        ProviderStub(lose_the_answer) as lossy,
        ProviderStub(lambda request: forward(mock.url, request)) as restarted,
    ):
        process = start_app(
            PROVIDER_BASE_URL=lossy.url,
            PROVIDER_TIMEOUT_SECONDS="60",
            BOOKING_POLL_ENABLED="0",
            **FAST_POLLING,
        )
        try:
            with make_app_client(process.url) as client, ThreadPoolExecutor(1) as pool:
                trip = create_planning_trip(client)
                _, item = add_item(client, trip["id"], 1, "offer-hotel-001")
                held = pool.submit(book_item, client, trip["id"], item)
                wait_until(lambda: "status" in accepted, "the mock to accept the booking")
                assert accepted["status"] == 201

                before = latest_attempt(db_engine, item["id"])
                assert_committed_unknown(before, item["id"], trip["id"])
                at_provider = provider.get(f"/bookings/by-reference/{before['client_reference']}")
                assert at_provider.status_code == 200  # it exists there, and the app does not know

                process.kill()
                release.set()
                with pytest.raises(httpx.TransportError):
                    held.result(timeout=10)
        finally:
            release.set()

        process.env["PROVIDER_BASE_URL"] = restarted.url
        process.env["BOOKING_POLL_ENABLED"] = "1"
        process.start()
        with make_app_client(process.url) as client:
            view = wait_until(
                lambda: (v := get_booking(client, trip["id"], item["id"]).json())["state"] == "confirmed"
                and v,
                "the poller to reconcile the attempt to confirmed",
            )
        assert view["booking"] == at_provider.json()
        assert posts(restarted) == []
        assert reference_reads(restarted)
        assert len(posts(lossy)) == 1
        after = latest_attempt(db_engine, item["id"])
        assert after["idempotency_key"] == before["idempotency_key"]
        assert after["client_reference"] == before["client_reference"]
        assert after["state"] == "confirmed"


@pytest.mark.parametrize(
    "offer_id,final",
    [("offer-hotel-002", "confirmed"), ("offer-hotel-003", "failed")],
    ids=["pending-to-confirmed", "pending-to-failed"],
)
def test_pending_booking_reaches_its_outcome_with_no_user_action(
    start_app, start_mock, db_engine, offer_id, final
):
    private = start_mock(MOCK_PENDING_DELAY_SECONDS="3")
    with ProviderStub(lambda request: forward(private.url, request)) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, **FAST_POLLING)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer_id)
            first = book_item(client, trip["id"], item)
            assert first.status_code == 201
            assert first.json()["state"] == "pending"
            pending_line = next(
                line
                for line in client.get(f"/trips/{trip['id']}/itinerary").json()["budget"]["lines"]
                if line["itemId"] == item["id"]
            )
            assert pending_line["priceKind"] == "estimate"

            view = wait_until(
                lambda: (v := get_booking(client, trip["id"], item["id"]).json())["state"] == final
                and v,
                f"the pending booking to become {final}",
                timeout=20,
            )
            assert view["booking"]["status"] == final
            assert "lastCheckedAt" in view
            assert "lastError" not in view
            if final == "failed":
                assert view["booking"]["failure"]["message"] in view["nextStep"]
            line = next(
                line
                for line in client.get(f"/trips/{trip['id']}/itinerary").json()["budget"]["lines"]
                if line["itemId"] == item["id"]
            )
            assert (line["priceKind"] == "confirmed") == (final == "confirmed")
        assert len(posts(stub)) == 1
        assert reference_reads(stub)
        assert latest_attempt(db_engine, item["id"])["check_count"] >= 1


READ_ANSWERS = {
    "404": lambda: StubResponse(404, provider_error("NOT_FOUND")),
    "429": lambda: StubResponse(429, provider_error("RATE_LIMITED"), {"Retry-After": "1"}),
    "503": lambda: StubResponse(503, provider_error("PROVIDER_UNAVAILABLE")),
    "undeclared": lambda: StubResponse(418, provider_error()),
    "non-json": lambda: StubResponse(200, b"not json", {"Content-Type": "text/plain"}),
    "schema-invalid": lambda: StubResponse(200, {"status": "confirmed", "extra": True}),
    "wrong-reference": lambda: None,
    "timeout": lambda: None,
}


@pytest.mark.parametrize("answer", READ_ANSWERS)
def test_reads_that_prove_nothing_never_change_an_unknown_attempt(start_app, db_engine, answer):
    offer = stub_offer("read-offer")
    release = threading.Event()

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if request["method"] == "POST":
            return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))
        if answer == "timeout":
            release.wait(5)
            return StubResponse(disconnect=True)
        if answer == "wrong-reference":
            return StubResponse(200, booking_for_reference("someone-elses-reference", offer))
        return READ_ANSWERS[answer]()

    with ProviderStub(respond) as stub:
        timeout = {"PROVIDER_TIMEOUT_SECONDS": "0.2"} if answer == "timeout" else {}
        process = start_app(PROVIDER_BASE_URL=stub.url, **timeout, **FAST_POLLING)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            assert book_item(client, trip["id"], item).json()["state"] == "unknown"
            try:
                wait_until(
                    lambda: latest_attempt(db_engine, item["id"])["check_count"] >= 3,
                    "three background checks",
                )
                wait_until(
                    lambda: latest_attempt(db_engine, item["id"])["last_checked_at"] is not None,
                    "last_checked_at to be recorded",
                )
            finally:
                release.set()
            view = get_booking(client, trip["id"], item["id"]).json()
            assert view["state"] == "unknown"
            assert "booking" not in view
            assert "lastCheckedAt" in view
            row = latest_attempt(db_engine, item["id"])
            assert row["state"] == "unknown" and row["booking"] is None
            wait_until(lambda: len(reference_reads(stub)) >= 3, "three by-reference reads to arrive")
        assert len(posts(stub)) == 1  # only the user's click ever POSTed


def test_polling_resumes_from_the_database_after_a_hard_kill(start_app, db_engine):
    offer = stub_offer("resume-offer")
    provider_has_it = threading.Event()

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if request["method"] == "POST":
            return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))
        if provider_has_it.is_set():
            return StubResponse(200, booking_for_reference(request["path"].rsplit("/", 1)[1], offer))
        return StubResponse(404, provider_error("NOT_FOUND"))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, **FAST_POLLING)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            assert book_item(client, trip["id"], item).json()["state"] == "unknown"
            wait_until(
                lambda: latest_attempt(db_engine, item["id"])["check_count"] >= 1,
                "the first background check",
            )
            assert get_booking(client, trip["id"], item["id"]).json()["state"] == "unknown"

        process.kill()
        checked = latest_attempt(db_engine, item["id"])["check_count"]
        provider_has_it.set()
        process.start()
        with make_app_client(process.url) as client:
            view = wait_until(
                lambda: (v := get_booking(client, trip["id"], item["id"]).json())["state"] == "confirmed"
                and v,
                "the restarted loop to adopt the booking",
            )
        assert view["booking"]["status"] == "confirmed"
        assert latest_attempt(db_engine, item["id"])["check_count"] > checked
        assert len(posts(stub)) == 1


def test_disabled_poller_changes_nothing_and_the_same_setup_converges_when_enabled(
    start_app, db_engine
):
    offer = stub_offer("disabled-offer")

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if request["method"] == "POST":
            return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))
        return StubResponse(200, booking_for_reference(request["path"].rsplit("/", 1)[1], offer))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0", **FAST_POLLING)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            assert book_item(client, trip["id"], item).json()["state"] == "unknown"

            def untouched():
                row = latest_attempt(db_engine, item["id"])
                return (
                    row["state"] == "unknown"
                    and row["check_count"] == 0
                    and row["last_checked_at"] is None
                    and not reference_reads(stub)
                )

            # Seven poll intervals: a running loop would have adopted the booking long before.
            stays_true(untouched, "BOOKING_POLL_ENABLED=0 still touched the attempt", window=1.5)

        # Control: the identical setup with the loop on does converge, so the quiet window above meant something.
        process.kill()
        process.env["BOOKING_POLL_ENABLED"] = "1"
        process.start()
        with make_app_client(process.url) as client:
            wait_until(
                lambda: get_booking(client, trip["id"], item["id"]).json()["state"] == "confirmed",
                "the enabled loop to adopt the booking",
            )
        assert len(posts(stub)) == 1


def test_late_pending_post_answer_never_undoes_a_confirmation_the_poller_stored(
    start_app, db_engine
):
    offer = stub_offer("late-offer")
    release = threading.Event()

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if request["method"] == "POST":
            release.wait(30)
            return StubResponse(201, provider_booking(request, offer, "pending"))
        return StubResponse(200, booking_for_reference(request["path"].rsplit("/", 1)[1], offer))

    with ProviderStub(respond) as stub:
        # One quick check, then a minute: the poller cannot repair a wrong state before the asserts below.
        process = start_app(PROVIDER_BASE_URL=stub.url, PROVIDER_TIMEOUT_SECONDS="60", BOOKING_POLL_INTERVALS="0.2,60")
        with make_app_client(process.url) as client, ThreadPoolExecutor(1) as pool:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            held = pool.submit(book_item, client, trip["id"], item)
            try:
                wait_until(
                    lambda: (latest_attempt(db_engine, item["id"]) or {}).get("state") == "confirmed",
                    "the poller to store confirmed while the POST is still held",
                )
                assert not held.done()
            finally:
                release.set()
            late = held.result(timeout=15)
            assert late.status_code == 201
            row = latest_attempt(db_engine, item["id"])
            assert row["state"] == "confirmed"
            assert row["booking"]["status"] == "confirmed"
            view = get_booking(client, trip["id"], item["id"]).json()
            assert view["state"] == "confirmed" and view["booking"]["status"] == "confirmed"


def test_poll_schedule_follows_the_configured_intervals(start_app, db_engine):
    offer = stub_offer("schedule-offer")

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if request["method"] == "POST":
            return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))
        return StubResponse(404, provider_error("NOT_FOUND"))

    def seconds(expression, item_id):
        with db_engine.connect() as connection:
            return float(
                connection.scalar(
                    text(f"SELECT EXTRACT(EPOCH FROM ({expression})) FROM booking_attempts WHERE item_id = :id"),
                    {"id": item_id},
                )
            )

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_INTERVALS="2,600")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            assert book_item(client, trip["id"], item).json()["state"] == "unknown"
            assert 1.5 <= seconds("next_check_at - created_at", item["id"]) <= 3.5  # the first interval, 2 s

            wait_until(
                lambda: latest_attempt(db_engine, item["id"])["check_count"] == 1,
                "the first check, due 2 s after the attempt",
            )
            assert seconds("next_check_at - now()", item["id"]) > 100  # the second interval, 600 s


@pytest.mark.parametrize(
    "headers,low,high",
    [
        pytest.param({"Retry-After": "600"}, 590, 601, id="retry-after-600"),
        pytest.param({}, 50, 61, id="no-header-uses-the-configured-interval"),
        pytest.param({"Retry-After": "soon"}, 50, 61, id="invalid-header-uses-the-configured-interval"),
    ],
)
def test_a_429_on_a_poll_waits_for_retry_after_and_otherwise_for_the_configured_interval(
    start_app, db_engine, headers, low, high
):
    offer = stub_offer("poll-429-offer")

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if request["method"] == "POST":
            return StubResponse(503, provider_error("PROVIDER_UNAVAILABLE"))
        return StubResponse(429, provider_error("RATE_LIMITED"), headers)

    with ProviderStub(respond) as stub:
        # The second interval is 60 s: with no usable Retry-After, that is how long the next check waits.
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_INTERVALS="0.2,60")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            assert book_item(client, trip["id"], item).json()["state"] == "unknown"
            wait_until(
                lambda: (latest_attempt(db_engine, item["id"]) or {}).get("last_checked_at") is not None,
                "the first check to finish",
            )
            with db_engine.connect() as connection:
                ahead = float(
                    connection.scalar(
                        text("SELECT EXTRACT(EPOCH FROM (next_check_at - now())) FROM booking_attempts WHERE item_id = :id"),
                        {"id": item["id"]},
                    )
                )
            assert low <= ahead <= high, ahead
            assert len(reference_reads(stub)) == 1  # and it really did wait: no second read yet
