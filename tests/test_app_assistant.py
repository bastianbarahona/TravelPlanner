"""Iteration 6 requirements 6-9: assistant route behavior, the candidate policy, model mode and read-only guarantees."""
import json

import httpx
import pytest

from app_support import (
    AppProcess,
    ProviderStub,
    StubResponse,
    add_participant,
    app_client as make_app_client,
    assert_error,
    check_app_response,
    create_planning_trip,
)
from booking_support import add_item, forward, seed_booked_trip, state_snapshot
from support import HTTP_OPTIONS, TOKEN_A

TOOL_NAMES = {"list_trips", "get_trip", "get_itinerary", "list_documents", "search_options"}


def assistant(client, trip_id, question, **extra):
    return client.post(
        f"/trips/{trip_id}/assistant",
        json={"question": question, **extra},
    )


def normalized_error(response, status, code):
    return {
        key: value
        for key, value in assert_error(response, status, code).items()
        if key != "requestId"
    }


def test_assistant_route_permissions_validation_and_viewer_access(application, app_client):
    trip = create_planning_trip(app_client)
    add_participant(app_client, trip["id"], "ben", "viewer")

    with httpx.Client(
        base_url=application.url,
        event_hooks={"response": [check_app_response]},
        **HTTP_OPTIONS,
    ) as anonymous:
        missing = assistant(anonymous, trip["id"], "What is the budget?")
    assert_error(missing, 401, "UNAUTHORIZED")
    assert missing.headers["www-authenticate"] == "Bearer"

    with make_app_client(application.url, "cam") as outsider:
        refused = assistant(outsider, trip["id"], "What is the budget?")
        ordinary = outsider.get(f"/trips/{trip['id']}")
    assert normalized_error(refused, 404, "NOT_FOUND") == normalized_error(
        ordinary, 404, "NOT_FOUND"
    )

    for response in (assistant(app_client, trip["id"], "   "), assistant(app_client, trip["id"], "x" * 1001)):
        body = assert_error(response, 422, "VALIDATION_FAILED")
        assert any(error["field"].split(".")[-1] == "question" for error in body["details"]["errors"]), body
    extra = assert_error(
        assistant(app_client, trip["id"], "What is the budget?", extra="forbidden"), 422, "VALIDATION_FAILED"
    )
    assert extra["details"]["errors"]

    # The boundary: 1000 characters are accepted, 1001 were refused above.
    assert assistant(app_client, trip["id"], "x" * 1000).status_code == 200

    with make_app_client(application.url, "ben") as viewer:
        allowed = assistant(viewer, trip["id"], "What is the budget?")
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert set(body) == {"tripId", "mode", "answer", "toolCalls"}
    assert body["tripId"] == trip["id"] and body["mode"] == "deterministic"
    assert isinstance(body["answer"], str) and body["answer"]
    assert isinstance(body["toolCalls"], list) and body["toolCalls"]


def test_tool_calls_record_the_real_mcp_calls_and_the_provider_is_searched_once_per_search_call(
    start_app, mock, control
):
    control.pin_clock("2030-01-15T12:00:00Z")
    with ProviderStub(lambda request: forward(mock.url, request)) as proxy:
        process = start_app(
            PROVIDER_BASE_URL=proxy.url, DOCUMENT_WORKER_ENABLED="0", BOOKING_POLL_ENABLED="0"
        )
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)  # one destination: one provider search per search_options call

            def provider_searches():
                return [request for request in proxy.requests if request["path"] == "/v1/offers/search"]

            general = assistant(client, trip["id"], "How is my budget looking?")
            assert general.status_code == 200, general.text
            assert general.json()["toolCalls"] == [
                {"name": "get_itinerary", "arguments": {"trip_id": trip["id"]}, "isError": False}
            ]
            assert provider_searches() == []

            single = assistant(client, trip["id"], "Please find a hotel for this trip")
            assert single.status_code == 200, single.text
            calls = single.json()["toolCalls"]
            assert calls[0] == {"name": "get_itinerary", "arguments": {"trip_id": trip["id"]}, "isError": False}
            searches = [call for call in calls if call["name"] == "search_options"]
            assert searches == [
                {
                    "name": "search_options",
                    "arguments": {"trip_id": trip["id"], "category": "accommodation"},
                    "isError": False,
                }
            ]
            assert all(call["name"] in TOOL_NAMES for call in calls)
            assert len(provider_searches()) == len(searches) == 1

            proxy.requests.clear()
            two = assistant(client, trip["id"], "I want a tour and a hotel")
            assert two.status_code == 200, two.text
            calls = two.json()["toolCalls"]
            searches = [call for call in calls if call["name"] == "search_options"]
            assert [call["arguments"]["category"] for call in searches] == ["accommodation", "activity"]
            assert all(call["name"] in TOOL_NAMES and call["isError"] is False for call in calls)
            assert len(provider_searches()) == len(searches) == 2


def recommended(answer):
    return [line for line in answer.splitlines() if line.startswith("- ")]


def option_line(offer_id, title, price, starts_at, ends_at, remaining):
    return (
        f"- {offer_id}: {title}, {price} USD, {starts_at} to {ends_at}. "
        f"Fits the remaining budget of {remaining} USD. Within the trip dates."
    )


# Computed from provider-mock/fixtures.json: Lisbon accommodation for 2 travelers in 2030-06-10..2030-06-13 is
# offer-hotel-001 420.00, -002 610.50, -003 380.00, -004 350.00 and -005 300.00 (not available). -006 is for 4 travelers.
HOTEL_001 = ("offer-hotel-001", "Three-night central hotel stay", "420.00", "2030-06-10T15:00:00+01:00", "2030-06-13T11:00:00+01:00")
HOTEL_002 = ("offer-hotel-002", "Riverside apartment, confirmed after a short wait", "610.50", "2030-06-10T16:00:00+01:00", "2030-06-13T10:00:00+01:00")
HOTEL_003 = ("offer-hotel-003", "Guesthouse, fails after a short wait", "380.00", "2030-06-10T14:00:00+01:00", "2030-06-13T11:00:00+01:00")
HOTEL_004 = ("offer-hotel-004", "Hostel, fails immediately", "350.00", "2030-06-10T14:00:00+01:00", "2030-06-13T10:00:00+01:00")


def test_candidates_are_the_cheapest_three_bookable_unplanned_offers_within_the_remaining_budget(
    application, app_client, control
):
    control.pin_clock("2030-01-15T12:00:00Z")

    # Nothing planned: four bookable hotels fit, the cap keeps the cheapest three, and 610.50 is the one left out.
    open_trip = create_planning_trip(app_client, budgetUsd="2000.00")
    answer = assistant(app_client, open_trip["id"], "Find a hotel").json()["answer"]
    assert recommended(answer) == [
        option_line(*HOTEL_004, "2000.00"),
        option_line(*HOTEL_003, "2000.00"),
        option_line(*HOTEL_001, "2000.00"),
    ]
    assert "offer-hotel-005" not in answer  # the cheapest offer, 300.00, is not available

    # One planned hotel: it is not offered again, so the fourth option moves up. Remaining is 2000.00 - 420.00.
    planned_trip = create_planning_trip(app_client, budgetUsd="2000.00")
    add_item(app_client, planned_trip["id"], 1, "offer-hotel-001")
    answer = assistant(app_client, planned_trip["id"], "Find a hotel").json()["answer"]
    assert recommended(answer) == [
        option_line(*HOTEL_004, "1580.00"),
        option_line(*HOTEL_003, "1580.00"),
        option_line(*HOTEL_002, "1580.00"),
    ]
    assert "offer-hotel-001" not in answer and "offer-hotel-005" not in answer

    # The boundary: remaining is exactly 350.00, so a 350.00 offer fits and a 380.00 offer does not.
    tight_trip = create_planning_trip(app_client, budgetUsd="770.00")
    add_item(app_client, tight_trip["id"], 1, "offer-hotel-001")
    answer = assistant(app_client, tight_trip["id"], "Find a hotel").json()["answer"]
    assert recommended(answer) == [option_line(*HOTEL_004, "350.00")]
    assert "offer-hotel-003" not in answer and "No suitable" not in answer


CLOSING = "Prices are provider estimates, not bookings. I cannot change the itinerary or book; use the application to do that."


def test_a_question_with_no_category_answers_the_budget_the_issues_and_the_hint_and_searches_nothing(
    app_client,
):
    trip = create_planning_trip(app_client)  # Lisbon, 1000.00, nothing planned
    response = assistant(app_client, trip["id"], "How is my budget looking?")
    assert response.status_code == 200, response.text
    assert response.json()["answer"].split("\n") == [
        "Budget: 0.00 of 1000.00 USD planned.",
        "Issue DESTINATION_WITHOUT_STAY: No accommodation is planned in Lisbon.",
        "Ask about transport, accommodation or activities to get options.",
        CLOSING,
    ]


def test_an_over_budget_trip_clamps_the_remaining_budget_at_zero_and_says_why_nothing_fits(
    application, app_client, control
):
    control.pin_clock("2030-01-15T12:00:00Z")
    trip = create_planning_trip(app_client, budgetUsd="300.00")
    add_item(app_client, trip["id"], 1, "offer-hotel-001")  # 420.00 against 300.00
    answer = assistant(app_client, trip["id"], "Find a hotel").json()["answer"].split("\n")
    # Five Lisbon hotels for two travelers (-006 is for four), one unavailable (-005), four over a remaining budget of 0.00.
    assert (
        "No suitable accommodation option found: 5 offers, 1 unavailable or expired, "
        "4 over the remaining budget of 0.00 USD."
    ) in answer
    assert "Budget: 420.00 of 300.00 USD planned. Over budget by 120.00 USD." in answer
    assert recommended("\n".join(answer)) == []


def test_a_failed_leg_is_disclosed_and_a_partial_leg_says_its_results_may_be_incomplete(
    application, app_client, control
):
    control.pin_clock("2030-01-15T12:00:00Z")
    trip = create_planning_trip(app_client)
    control.fault(503)
    failed = assistant(app_client, trip["id"], "Find a hotel").json()["answer"].split("\n")
    assert "Search for accommodation in Lisbon failed; no options from it." in failed
    assert recommended("\n".join(failed)) == []

    control.reset()
    control.pin_clock("2030-01-15T12:00:00Z")
    porto = create_planning_trip(app_client, destinations=["Porto"])  # the fixtures fail supplier-b for Porto
    partial = assistant(app_client, porto["id"], "Find a hotel").json()["answer"].split("\n")
    assert "Results for accommodation in Porto may be incomplete." in partial
    assert [line.split(":")[0] for line in recommended("\n".join(partial))] == ["- offer-porto-hotel-001"]


def test_assistant_changes_no_state_and_never_calls_booking(start_app, mock, control, db_engine):
    # ponytail: the real mock control API cannot expose its request log, so this proxy only observes and forwards.
    with ProviderStub(lambda request: forward(mock.url, request)) as proxy:
        process = start_app(
            PROVIDER_BASE_URL=proxy.url, DOCUMENT_WORKER_ENABLED="0", BOOKING_POLL_ENABLED="0"
        )
        with make_app_client(process.url) as client:
            trip = seed_booked_trip(client)
            before = state_snapshot(client, trip["id"], db_engine)
            proxy.requests.clear()

            response = assistant(client, trip["id"], "Find a hotel")
            assert response.status_code == 200, response.text
            assert any(call["name"] == "search_options" for call in response.json()["toolCalls"])
            after = state_snapshot(client, trip["id"], db_engine)

    assert after == before
    assert any(request["path"] == "/v1/offers/search" for request in proxy.requests)
    assert not any(request["path"] == "/v1/bookings" for request in proxy.requests)


def test_anthropic_without_credentials_is_503_and_invalid_mode_prevents_startup(
    start_app, postgres, mock, empty_database
):
    anthropic = start_app(
        ASSISTANT_MODE="anthropic", ANTHROPIC_API_KEY="", ANTHROPIC_AUTH_TOKEN="", DOCUMENT_WORKER_ENABLED="0"
    )
    with make_app_client(anthropic.url) as client:
        trip = create_planning_trip(client)
        response = assistant(client, trip["id"], "Find a hotel")
    body = assert_error(response, 503, "ASSISTANT_UNAVAILABLE")
    assert "answer" not in body and "toolCalls" not in body

    invalid = AppProcess(
        postgres.database_url,
        PROVIDER_BASE_URL=f"{mock.url}/v1",
        PROVIDER_TOKEN=TOKEN_A,
        ASSISTANT_MODE="invalid-mode",
        DOCUMENT_WORKER_ENABLED="0",
        BOOKING_POLL_ENABLED="0",
    )
    try:
        with pytest.raises(RuntimeError, match="application did not become ready"):
            invalid.start()
        assert invalid.process is None
        assert "ASSISTANT_MODE" in invalid.read_logs()  # it stopped for this reason, not for another
    finally:
        invalid.close()


# --- model mode, against a local stand-in for the Anthropic API ------------------------------------------------------


def model_message(content, stop_reason):
    return {
        "id": "msg_stub",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def text_block(text):
    return {"type": "text", "text": text}


class StubModel:
    """Serves scripted messages on POST /v1/messages and records every request the application sends."""

    def __init__(self, script):
        self.script = list(script)  # a list of bodies, or of callables that build one
        self.stub = ProviderStub(self.respond)

    def respond(self, request):
        assert request["method"] == "POST" and request["path"].split("?")[0] == "/v1/messages", request["path"]
        step = self.script.pop(0)
        if isinstance(step, StubResponse):
            return step
        return StubResponse(200, step() if callable(step) else step)

    def __enter__(self):
        self.stub.__enter__()
        self.url = self.stub.url.removesuffix("/v1")  # the SDK appends /v1/messages itself
        return self

    def __exit__(self, *exc):
        return self.stub.__exit__(*exc)

    @property
    def requests(self):
        return self.stub.requests


def start_model_app(start_app, model):
    return start_app(
        ASSISTANT_MODE="anthropic",
        ANTHROPIC_API_KEY="test",
        ANTHROPIC_AUTH_TOKEN="",
        ANTHROPIC_BASE_URL=model.url,
        DOCUMENT_WORKER_ENABLED="0",
        BOOKING_POLL_ENABLED="0",
    )


@pytest.mark.parametrize("reason", ["max_tokens", "stop_sequence"])
def test_model_mode_reports_a_stop_reason_that_is_not_an_answer_and_never_the_partial_text(start_app, reason):
    partial = "PARTIAL-TEXT-MARKER half an answ"
    with StubModel([model_message([text_block(partial)], reason)]) as model:
        process = start_model_app(start_app, model)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            response = assistant(client, trip["id"], "Find a hotel")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "anthropic"
    assert body["answer"] == f"I stopped before a complete answer (stop reason: {reason})."
    assert "PARTIAL-TEXT-MARKER" not in response.text
    assert body["toolCalls"] == [] and len(model.requests) == 1


def test_model_mode_runs_a_tool_use_turn_through_mcp_and_returns_the_final_text(start_app):
    state = {}

    def tool_use_turn():
        return model_message(
            [{"type": "tool_use", "id": "toolu_01", "name": "get_itinerary", "input": {"trip_id": state["trip_id"]}}],
            "tool_use",
        )

    with StubModel([tool_use_turn, model_message([text_block("FINAL-ANSWER")], "end_turn")]) as model:
        process = start_model_app(start_app, model)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            state["trip_id"] = trip["id"]
            response = assistant(client, trip["id"], "How is my budget looking?")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "anthropic" and body["answer"] == "FINAL-ANSWER"
    assert body["toolCalls"] == [
        {"name": "get_itinerary", "arguments": {"trip_id": trip["id"]}, "isError": False}
    ]

    first, second = (request["body"] for request in model.requests)
    assert model.requests[0]["headers"]["authorization"] == "Bearer test"
    assert {tool["name"] for tool in first["tools"]} == TOOL_NAMES
    assert trip["id"] in json.dumps(first["system"])
    result = second["messages"][-1]
    assert result["role"] == "user"
    block = result["content"][0]
    assert block["type"] == "tool_result" and block["tool_use_id"] == "toolu_01"
    assert not block.get("is_error")
    assert json.loads(block["content"])["tripId"] == trip["id"]  # the real MCP result, not a canned one


def test_model_mode_sends_pause_turn_back_and_counts_it_as_a_turn(start_app):
    script = [
        model_message([text_block("PAUSE-MARKER")], "pause_turn"),
        model_message([text_block("FINAL-ANSWER")], "end_turn"),
    ]
    with StubModel(script) as model:
        process = start_model_app(start_app, model)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            response = assistant(client, trip["id"], "Find a hotel")
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "FINAL-ANSWER"
    assert len(model.requests) == 2
    assert model.requests[1]["body"]["messages"][-1]["role"] == "assistant"  # the paused turn went back


def test_model_mode_answers_the_declined_text_on_a_refusal_and_not_the_models_text(start_app):
    with StubModel([model_message([text_block("REFUSAL-MARKER")], "refusal")]) as model:
        process = start_model_app(start_app, model)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            response = assistant(client, trip["id"], "Find a hotel")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "declined" in body["answer"].lower()
    assert "REFUSAL-MARKER" not in response.text
    assert body["toolCalls"] == []


def test_model_mode_answers_503_and_no_answer_when_the_model_returns_an_error(start_app):
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": "stub refusal to serve"}}
    with StubModel([StubResponse(400, error)]) as model:
        process = start_model_app(start_app, model)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            response = assistant(client, trip["id"], "Find a hotel")
    body = assert_error(response, 503, "ASSISTANT_UNAVAILABLE")
    assert "answer" not in body and "toolCalls" not in body and "stub refusal" not in response.text
    assert len(model.requests) == 1  # a 400 is not retried
