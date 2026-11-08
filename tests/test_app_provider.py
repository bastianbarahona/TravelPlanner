"""Iteration 3 requirements 1-6: provider parsing, search, requests and retries."""
import json
import threading
import time

import httpx
import pytest

from app_support import (
    AppProcess,
    ProviderStub,
    StubResponse,
    app_client as make_app_client,
    assert_error,
    create_planning_trip,
    free_port,
    provider_error,
    stub_offer,
    stub_search_result,
)
from support import HTTP_OPTIONS
from support import search_body as provider_search_body


def search(client, trip, **body):
    return client.post(
        f"/trips/{trip['id']}/search",
        json={"category": "accommodation"} | body,
    )


def add(client, trip, offer_id="offer-hotel-001", version=1):
    return client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={"version": version, "offerId": offer_id},
    )


def refresh(client, trip, item, version):
    return client.post(
        f"/trips/{trip['id']}/itinerary/items/{item['id']}/refresh",
        json={"version": version},
    )


INVALID_OFFERS = [
    pytest.param({"extra": 1}, id="extra-field"),
    pytest.param({"origin": None}, id="explicit-null"),
    pytest.param({"price": {"amount": 420, "currency": "USD"}}, id="number-amount"),
    pytest.param({"price": {"amount": "420", "currency": "USD"}}, id="amount-without-decimals"),
    pytest.param({"price": {"amount": "420.00\n", "currency": "USD"}}, id="newline-after-amount"),
    pytest.param({"startsAt": "2030-06-10T15:00:00"}, id="naive-instant"),
    pytest.param({"endsAt": "2030-06-13T11:00:00+01:00\n"}, id="newline-after-instant"),
    pytest.param(
        {"price": {"amount": "10000000000.00", "currency": "USD"}},
        id="amount-over-application-limit",
    ),
]


@pytest.fixture(scope="module")
def unusable_provider_app(postgres):
    with ProviderStub(lambda _request: StubResponse(500)) as stub:
        process = AppProcess(
            postgres.database_url,
            PROVIDER_BASE_URL=stub.url,
            PROVIDER_TOKEN="token-a",
        )
        process.start()
        try:
            yield process, stub
        finally:
            process.kill()


@pytest.mark.parametrize("workflow", ["search", "get-offer"])
@pytest.mark.parametrize("changes", INVALID_OFFERS)
def test_schema_invalid_success_is_never_displayed_or_saved(
    app_client, control, workflow, changes
):
    control.change_offer("offer-hotel-001", **changes)
    trip = create_planning_trip(app_client)

    if workflow == "search":
        response = search(app_client, trip)
        assert response.status_code == 200
        leg = response.json()["legs"][0]
        assert leg["status"] == "failed"
        assert leg["options"] == []
        assert leg["error"]["code"] == "PROVIDER_INVALID_RESPONSE"
    else:
        assert_error(add(app_client, trip), 502, "PROVIDER_INVALID_RESPONSE")

    itinerary = app_client.get(f"/trips/{trip['id']}/itinerary").json()
    assert itinerary["items"] == []
    assert itinerary["version"] == 1


def test_maximum_amount_works_end_to_end_on_the_mock(app_client, control):
    expected = control.change_offer(
        "offer-hotel-001",
        price={"amount": "9999999999.99", "currency": "USD"},
    )
    trip = create_planning_trip(app_client, budgetUsd="9999999999.99")
    result = search(app_client, trip).json()
    option = next(option for option in result["legs"][0]["options"] if option["offer"]["id"] == expected["id"])
    assert option["offer"] == expected

    created = add(app_client, trip)
    assert created.status_code == 201
    assert created.json()["items"][0]["offer"] == expected
    assert created.json()["budget"]["totalUsd"] == "9999999999.99"


def test_lowercase_instants_work_for_search_and_add_through_the_stub(start_app, control):
    expected = stub_offer(
        startsAt="2030-06-10t15:00:00z",
        endsAt="2030-06-13t11:00:00z",
        validUntil="2099-01-01t00:00:00z",
    )

    def respond(request):
        if request["method"] == "POST":
            return StubResponse(200, stub_search_result(expected))
        return StubResponse(200, expected)

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            leg = search(client, trip).json()["legs"][0]
            assert leg["status"] == "complete"
            assert leg["options"][0]["offer"] == expected
            created = add(client, trip, expected["id"])
            assert created.status_code == 201
            assert created.json()["items"][0]["offer"] == expected


@pytest.mark.parametrize("workflow", ["search", "get-offer"])
@pytest.mark.parametrize("location", ["value", "nested-value", "key"])
@pytest.mark.parametrize(
    "bad_text",
    [pytest.param("\x00", id="nul"), pytest.param("\ud800", id="lone-surrogate")],
)
def test_unusable_provider_strings_are_invalid_and_never_saved(
    unusable_provider_app, control, workflow, location, bad_text
):
    offer = stub_offer()
    if location == "value":
        offer["title"] = bad_text
    elif location == "nested-value":
        offer["restrictions"] = [bad_text]
    else:
        offer[bad_text] = offer.pop("title")

    def respond(request):
        body = stub_search_result(offer) if request["method"] == "POST" else offer
        return StubResponse(200, body)

    process, stub = unusable_provider_app
    stub.responder = respond
    with make_app_client(process.url) as client:
        trip = create_planning_trip(client)
        if workflow == "search":
            leg = search(client, trip).json()["legs"][0]
            assert leg["status"] == "failed"
            assert leg["error"]["code"] == "PROVIDER_INVALID_RESPONSE"
            assert leg["options"] == []
        else:
            assert_error(add(client, trip, "stub-offer"), 502, "PROVIDER_INVALID_RESPONSE")
        view = client.get(f"/trips/{trip['id']}/itinerary").json()
        assert view["items"] == []
        assert view["version"] == 1


@pytest.mark.parametrize("workflow", ["search", "get-offer"])
@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(StubResponse(200, b"not json", {"content-type": "text/plain"}), id="non-json"),
        pytest.param(StubResponse(418, provider_error()), id="undeclared-status"),
        pytest.param(StubResponse(503, {"code": "PROVIDER_UNAVAILABLE"}), id="invalid-error-body"),
    ],
)
def test_unparseable_undeclared_and_invalid_error_responses_are_refused(
    start_app, control, workflow, reply
):
    with ProviderStub(lambda _request: reply) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            if workflow == "search":
                response = search(client, trip)
                assert response.status_code == 200
                leg = response.json()["legs"][0]
                assert leg["status"] == "failed"
                assert leg["error"]["code"] == "PROVIDER_INVALID_RESPONSE"
                assert leg["options"] == []
            else:
                assert_error(add(client, trip), 502, "PROVIDER_INVALID_RESPONSE")
            assert client.get(f"/trips/{trip['id']}/itinerary").json()["items"] == []


def test_search_distinguishes_every_leg_state_and_keeps_successes(app_client, control):
    trip = create_planning_trip(
        app_client,
        destinations=["Lisbon", "Porto", "Coimbra", "Atlantis", "Evora"],
    )
    response = search(app_client, trip)
    assert response.status_code == 200
    legs = response.json()["legs"]
    assert [leg["destination"] for leg in legs] == trip["destinations"]
    assert [leg["status"] for leg in legs] == ["complete", "partial", "partial", "empty", "failed"]
    assert legs[0]["options"]
    assert legs[1]["options"] and legs[1]["warnings"]
    assert legs[2]["options"] == [] and legs[2]["warnings"]
    assert legs[3]["options"] == [] and legs[3]["warnings"] == []
    assert legs[4]["options"] == [] and legs[4]["warnings"] == []
    assert "searchedAt" not in legs[4]
    assert legs[4]["error"]["code"] == "PROVIDER_UNAVAILABLE"


def test_search_keeps_provider_offers_warnings_and_freshness_unchanged(
    app_client, provider, control
):
    control.pin_clock("2030-01-15T12:00:00Z")
    expected = provider.post(
        "/offers/search", json=provider_search_body(destination="Porto")
    ).json()
    trip = create_planning_trip(app_client, destinations=["Porto"])
    leg = search(app_client, trip).json()["legs"][0]
    assert [option["offer"] for option in leg["options"]] == expected["offers"]
    assert leg["warnings"] == expected["warnings"]
    assert leg["searchedAt"] == expected["searchedAt"]


def test_rate_limit_unknown_supplier_and_provider_auth_are_mapped(
    app_client, start_app, control
):
    trip = create_planning_trip(app_client, destinations=["Braga"])
    limited = search(app_client, trip, providerId="supplier-b").json()["legs"][0]
    assert limited["status"] == "failed"
    assert limited["error"]["code"] == "RATE_LIMITED"
    assert limited["error"]["retryAfterSeconds"] >= 1

    unknown = search(app_client, trip, providerId="not-a-supplier").json()["legs"][0]
    assert unknown["status"] == "failed"
    assert unknown["error"]["code"] == "BAD_REQUEST"
    assert "supplier" in unknown["error"]["message"].lower()

    bad_process = start_app(PROVIDER_TOKEN="wrong")
    with make_app_client(bad_process.url) as bad_client:
        bad_trip = create_planning_trip(bad_client)
        auth = search(bad_client, bad_trip).json()["legs"][0]
        assert auth["status"] == "failed"
        assert auth["error"]["code"] == "PROVIDER_AUTH"

    assert search(app_client, trip, destinations=["Lisbon"]).json()["legs"][0]["status"] == "complete"


def test_slow_supplier_warning_and_short_deadline_timeout(start_app, control):
    process = start_app(PROVIDER_TIMEOUT_SECONDS="0.1")
    with make_app_client(process.url) as client:
        trip = create_planning_trip(client, destinations=["Sintra"])
        timed_out = search(client, trip).json()["legs"][0]
        assert timed_out["status"] == "failed"
        assert timed_out["error"]["code"] == "PROVIDER_UNREACHABLE"

    normal = start_app()
    with make_app_client(normal.url) as client:
        trip = create_planning_trip(client, destinations=["Faro"])
        partial = search(client, trip).json()["legs"][0]
        assert partial["status"] == "partial"
        assert any(warning["code"] == "PROVIDER_TIMEOUT" for warning in partial["warnings"])


def test_connection_refusal_and_dripping_body_are_cut_off(start_app, control):
    refused_port = free_port()
    refused = start_app(
        PROVIDER_BASE_URL=f"http://127.0.0.1:{refused_port}/v1",
        PROVIDER_TIMEOUT_SECONDS="0.2",
    )
    with make_app_client(refused.url) as client:
        trip = create_planning_trip(client)
        leg = search(client, trip).json()["legs"][0]
        assert leg["error"]["code"] == "PROVIDER_UNREACHABLE"

    raw = json.dumps(stub_search_result()).encode()
    chunks = [raw[index : index + 10] for index in range(0, len(raw), 10)]
    with ProviderStub(lambda _request: StubResponse(200, chunks=chunks, delay=0.08)) as stub:
        dripping = start_app(PROVIDER_BASE_URL=stub.url, PROVIDER_TIMEOUT_SECONDS="0.25")
        with make_app_client(dripping.url) as client:
            trip = create_planning_trip(client)
            started = time.monotonic()
            leg = search(client, trip).json()["legs"][0]
            elapsed = time.monotonic() - started
        assert leg["error"]["code"] == "PROVIDER_UNREACHABLE"
        assert elapsed < 1.0


@pytest.mark.parametrize("workflow", ["add", "refresh"])
def test_add_and_refresh_map_provider_auth_to_502(start_app, control, workflow):
    attempts = 0

    def respond(_request):
        nonlocal attempts
        attempts += 1
        if workflow == "refresh" and attempts == 1:
            return StubResponse(200, stub_offer("mapped-offer"))
        return StubResponse(401, provider_error("UNAUTHORIZED"))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            if workflow == "refresh":
                before = add(client, trip, "mapped-offer").json()
                response = refresh(client, trip, before["items"][0], before["version"])
            else:
                before = client.get(f"/trips/{trip['id']}/itinerary").json()
                response = add(client, trip, "mapped-offer")
            assert response.status_code != 401
            assert_error(response, 502, "PROVIDER_AUTH")
            assert client.get(f"/trips/{trip['id']}/itinerary").json() == before


@pytest.mark.parametrize("workflow", ["add", "refresh"])
def test_add_and_refresh_map_a_hung_provider_to_unreachable(
    start_app, control, workflow
):
    release = threading.Event()
    attempts = 0

    def respond(_request):
        nonlocal attempts
        attempts += 1
        if workflow == "refresh" and attempts == 1:
            return StubResponse(200, stub_offer("mapped-offer"))
        release.wait(5)
        return StubResponse(200, stub_offer("mapped-offer"))

    with ProviderStub(respond) as stub:
        if workflow == "refresh":
            setup = start_app(PROVIDER_BASE_URL=stub.url)
            with make_app_client(setup.url) as client:
                trip = create_planning_trip(client)
                before = add(client, trip, "mapped-offer").json()
        process = start_app(
            PROVIDER_BASE_URL=stub.url, PROVIDER_TIMEOUT_SECONDS="0.2"
        )
        with make_app_client(process.url) as client:
            if workflow == "refresh":
                call = lambda: refresh(
                    client, trip, before["items"][0], before["version"]
                )
            else:
                trip = create_planning_trip(client)
                before = client.get(f"/trips/{trip['id']}/itinerary").json()
                call = lambda: add(client, trip, "mapped-offer")
            try:
                response = call()
            finally:
                release.set()
            assert_error(response, 503, "PROVIDER_UNREACHABLE")
            assert client.get(f"/trips/{trip['id']}/itinerary").json() == before


@pytest.mark.parametrize("workflow", ["add", "refresh"])
def test_add_and_refresh_map_connection_refusal_to_unreachable(
    start_app, control, workflow
):
    setup = start_app()
    with make_app_client(setup.url) as client:
        trip = create_planning_trip(client)
        if workflow == "refresh":
            before = add(client, trip).json()
        else:
            before = client.get(f"/trips/{trip['id']}/itinerary").json()

    refused = start_app(
        PROVIDER_BASE_URL=f"http://127.0.0.1:{free_port()}/v1",
        PROVIDER_TIMEOUT_SECONDS="0.2",
    )
    with make_app_client(refused.url) as client:
        if workflow == "refresh":
            response = refresh(client, trip, before["items"][0], before["version"])
        else:
            response = add(client, trip)
        assert_error(response, 503, "PROVIDER_UNREACHABLE")
        assert client.get(f"/trips/{trip['id']}/itinerary").json() == before


def test_refresh_preserves_retry_after_when_rate_limited(start_app, control):
    attempts = 0

    def respond(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return StubResponse(200, stub_offer("limited-offer"))
        return StubResponse(
            429, provider_error("RATE_LIMITED"), {"Retry-After": "6"}
        )

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            before = add(client, trip, "limited-offer").json()
            response = refresh(client, trip, before["items"][0], before["version"])
            body = assert_error(response, 429, "RATE_LIMITED")
            assert response.headers["retry-after"] == "6"
            assert body["details"]["retryAfterSeconds"] == 6
            assert client.get(f"/trips/{trip['id']}/itinerary").json() == before


def test_search_sends_exact_bodies_once_and_never_sends_null(start_app, control):
    with ProviderStub(lambda _request: StubResponse(503, provider_error())) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client, travelers=3)
            result = search(
                client,
                trip,
                origin="  ignored  ",
                destinations=[" Lisbon ", "Porto", "Coimbra"],
            )
            assert result.status_code == 200
            patched = client.patch(
                f"/trips/{trip['id']}", json={"version": 1, "travelers": 4}
            )
            assert patched.status_code == 200
            transport = search(
                client,
                trip,
                category="transport",
                origin="  London  ",
                destinations=["Lisbon"],
            )
            assert transport.status_code == 200

        assert len(stub.requests) == 4
        accommodation = stub.requests[:3]
        assert {request["body"]["destination"] for request in accommodation} == {
            "Lisbon",
            "Porto",
            "Coimbra",
        }
        for request in accommodation:
            assert request["method"] == "POST"
            assert request["path"] == "/v1/offers/search"
            assert request["body"]["category"] == "accommodation"
            assert request["body"]["startsOn"] == "2030-06-10"
            assert request["body"]["endsOn"] == "2030-06-13"
            assert request["body"]["travelers"] == 3
            assert "origin" not in request["body"]
            assert "providerId" not in request["body"]
            assert None not in request["body"].values()
            assert request["headers"]["authorization"] == "Bearer token-a"
        assert stub.requests[3]["body"]["category"] == "transport"
        assert stub.requests[3]["body"]["destination"] == "Lisbon"
        assert stub.requests[3]["body"]["origin"] == "London"
        assert stub.requests[3]["body"]["travelers"] == 4


def test_offer_id_is_percent_encoded_as_one_path_segment(start_app, control):
    opaque = "offer-hotel-001/../offer-hotel-002"

    def respond(request):
        return StubResponse(200, stub_offer(opaque))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            response = add(client, trip, opaque)
            assert response.status_code == 201
            assert response.json()["items"][0]["offer"]["id"] == opaque
        assert [request["path"] for request in stub.requests] == [
            "/v1/offers/offer-hotel-001%2F..%2Foffer-hotel-002"
        ]


def test_every_price_is_an_estimate_and_searched_at_is_provider_time(app_client, control):
    control.pin_clock("2030-01-15T12:00:00Z")
    trip = create_planning_trip(app_client)
    leg = search(app_client, trip).json()["legs"][0]
    assert leg["searchedAt"] == "2030-01-15T12:00:00Z"
    assert leg["options"] and {option["priceKind"] for option in leg["options"]} == {"estimate"}

    created = add(app_client, trip)
    assert created.status_code == 201
    view = created.json()
    assert {item["priceKind"] for item in view["items"]} == {"estimate"}
    assert {line["priceKind"] for line in view["budget"]["lines"]} == {"estimate"}
    assert view["budget"]["confirmedUsd"] == "0.00"


def test_get_offer_retries_429_after_the_full_retry_after_and_then_succeeds(
    app_client, control, mock
):
    trip = create_planning_trip(app_client)
    control.fault(429)

    def clear_fault():
        time.sleep(1.0)
        httpx.post(f"{mock.url}/__control/fault", json={"status": None}, **HTTP_OPTIONS).raise_for_status()

    thread = threading.Thread(target=clear_fault)
    thread.start()
    started = time.monotonic()
    response = add(app_client, trip)
    elapsed = time.monotonic() - started
    thread.join()
    assert response.status_code == 201
    assert elapsed >= 1.9


def test_get_offer_stops_after_two_503_attempts_and_maps_the_error(start_app, control):
    with ProviderStub(lambda _request: StubResponse(503, provider_error())) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            started = time.monotonic()
            response = add(client, trip)
            elapsed = time.monotonic() - started
        assert_error(response, 503, "PROVIDER_UNAVAILABLE")
        assert len(stub.requests) == 2
        assert 2 <= elapsed < 5


def test_held_rate_limit_gives_up_after_two_attempts(app_client, control):
    trip = create_planning_trip(app_client)
    control.fault(429)
    started = time.monotonic()
    response = add(app_client, trip)
    elapsed = time.monotonic() - started
    body = assert_error(response, 429, "RATE_LIMITED")
    assert response.headers["retry-after"] == "2"
    assert body["details"]["retryAfterSeconds"] == 2
    assert 1.9 <= elapsed < 4
    view = app_client.get(f"/trips/{trip['id']}/itinerary").json()
    assert view["version"] == 1
    assert view["items"] == []


@pytest.mark.parametrize("retry_after", [60, 6])
def test_long_retry_after_is_reported_without_sleep_or_retry(
    start_app, control, retry_after
):
    reply = StubResponse(
        429, provider_error("RATE_LIMITED"), {"Retry-After": str(retry_after)}
    )
    with ProviderStub(lambda _request: reply) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            started = time.monotonic()
            response = add(client, trip)
            elapsed = time.monotonic() - started
        body = assert_error(response, 429, "RATE_LIMITED")
        assert response.headers["retry-after"] == str(retry_after)
        assert body["details"]["retryAfterSeconds"] == retry_after
        assert len(stub.requests) == 1
        assert elapsed < 1.5


def test_ten_digit_retry_after_is_reported_without_sleep_or_retry(start_app, control):
    reply = StubResponse(
        429, provider_error("RATE_LIMITED"), {"Retry-After": "9999999999"}
    )
    with ProviderStub(lambda _request: reply) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            started = time.monotonic()
            response = add(client, trip)
            elapsed = time.monotonic() - started
        body = assert_error(response, 429, "RATE_LIMITED")
        assert response.headers["retry-after"] == "9999999999"
        assert body["details"]["retryAfterSeconds"] == 9999999999
        assert len(stub.requests) == 1
        assert elapsed < 1.5


def test_retry_after_five_retries_once_then_returns_the_second_429(start_app, control):
    reply = StubResponse(429, provider_error("RATE_LIMITED"), {"Retry-After": "5"})
    with ProviderStub(lambda _request: reply) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            started = time.monotonic()
            response = add(client, trip)
            elapsed = time.monotonic() - started
        body = assert_error(response, 429, "RATE_LIMITED")
        assert response.headers["retry-after"] == "5"
        assert body["details"]["retryAfterSeconds"] == 5
        assert len(stub.requests) == 2
        assert elapsed >= 4.9


def test_invalid_retry_after_uses_five_seconds_before_retry(start_app, control):
    attempts = 0

    def respond(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return StubResponse(429, provider_error("RATE_LIMITED"), {"Retry-After": "later"})
        return StubResponse(200, stub_offer())

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            started = time.monotonic()
            response = add(client, trip, "stub-offer")
            elapsed = time.monotonic() - started
        assert response.status_code == 201
        assert len(stub.requests) == 2
        assert elapsed >= 4.9


def test_get_offer_does_not_retry_when_a_503_wait_would_miss_the_deadline(
    start_app, control
):
    def respond(_request):
        time.sleep(0.7)
        return StubResponse(503, provider_error())

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, PROVIDER_TIMEOUT_SECONDS="1")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            started = time.monotonic()
            response = add(client, trip)
            elapsed = time.monotonic() - started
        assert_error(response, 503, "PROVIDER_UNAVAILABLE")
        assert len(stub.requests) == 1
        assert elapsed < 1.5


def test_get_offer_second_attempt_shares_the_first_attempt_deadline(
    start_app, control
):
    release = threading.Event()
    attempts = 0

    def respond(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return StubResponse(
                429, provider_error("RATE_LIMITED"), {"Retry-After": "2"}
            )
        release.wait(5)
        return StubResponse(200, stub_offer())

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, PROVIDER_TIMEOUT_SECONDS="3")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            started = time.monotonic()
            try:
                response = add(client, trip)
            finally:
                release.set()
            elapsed = time.monotonic() - started
        assert_error(response, 503, "PROVIDER_UNREACHABLE")
        assert len(stub.requests) == 2
        assert elapsed < 3.5
