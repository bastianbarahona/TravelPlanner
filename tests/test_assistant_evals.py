"""The deterministic assistant evaluation, and one negative control per defect of every binary check.

Each control starts from a baseline observation the check accepts, then changes one thing. The baseline passing proves
the control fails for that one change and not for something else.
"""
from dataclasses import replace
from types import SimpleNamespace

from assistant_evals import (
    TOOL_NAMES,
    Observation,
    called_expected_tools,
    describe_tools,
    discloses_failure,
    grounded,
    mentions_issues,
    read_only,
    refuses_outsider,
    run_evaluations,
    says_no_option,
)

CHECK_NAMES = {
    "called_expected_tools",
    "grounded",
    "says_no_option",
    "discloses_failure",
    "mentions_issues",
    "refuses_outsider",
    "read_only",
}
EXPECTED_RESULTS = [
    ("E1", "called_expected_tools"),
    ("E1", "grounded"),
    ("E2", "called_expected_tools"),
    ("E2", "grounded"),
    ("E2", "says_no_option"),
    ("E3", "called_expected_tools"),
    ("E3", "grounded"),
    ("E3", "discloses_failure"),
    ("E4", "called_expected_tools"),
    ("E4", "grounded"),
    ("E4", "mentions_issues"),
    ("E5", "called_expected_tools"),
    ("E5", "grounded"),
    ("E6", "refuses_outsider"),
    ("E7", "called_expected_tools"),
    ("E7", "read_only"),
]


def test_deterministic_evaluation_scores_exactly_the_expected_checks_at_100_percent():
    report = run_evaluations("deterministic")
    assert [(result.case_id, result.check) for result in report.results] == EXPECTED_RESULTS
    assert {result.check for result in report.results} == CHECK_NAMES
    assert len(report.results) == 16
    assert report.failed == []
    assert report.passed == 16


# --- called_expected_tools -----------------------------------------------------------------------------------------

ITINERARY_CALL = {"name": "get_itinerary", "arguments": {"trip_id": "trip-1"}, "isError": False}
SEARCH_CALL = {
    "name": "search_options",
    "arguments": {"trip_id": "trip-1", "category": "accommodation"},
    "isError": False,
}


def calls_observation(**changes):
    base = Observation(
        case_id="control",
        trip_id="trip-1",
        expected_categories=("accommodation",),
        tool_calls=[ITINERARY_CALL, SEARCH_CALL],
    )
    return replace(base, **changes)


def test_called_expected_tools_baseline_passes():
    assert called_expected_tools(calls_observation()) is True
    assert called_expected_tools(calls_observation(expected_categories=(), tool_calls=[ITINERARY_CALL])) is True


def test_called_expected_tools_control_rejects_a_missing_search_call():
    assert called_expected_tools(calls_observation(tool_calls=[ITINERARY_CALL])) is False


def test_called_expected_tools_control_rejects_a_search_call_where_none_is_expected():
    bad = calls_observation(expected_categories=(), tool_calls=[ITINERARY_CALL, SEARCH_CALL])
    assert called_expected_tools(bad) is False


def test_called_expected_tools_control_rejects_a_tool_error():
    bad = calls_observation(tool_calls=[ITINERARY_CALL, {**SEARCH_CALL, "isError": True}])
    assert called_expected_tools(bad) is False


# --- grounded ----------------------------------------------------------------------------------------------------

OPTION_RESULTS = [
    {
        "budget": {"budgetUsd": "1000.00", "totalUsd": "420.00"},
        "legs": [
            {
                "options": [
                    {
                        "offer": {
                            "id": "offer-hotel-001",
                            "category": "accommodation",
                            "price": {"amount": "420.00", "currency": "USD"},
                        },
                        "bookable": True,
                    }
                ]
            }
        ],
    }
]
GROUNDED_ANSWER = (
    "- offer-hotel-001: Hotel, 420.00 USD, 2030-06-10 to 2030-06-13. Fits the remaining budget of 580.00 USD."
)


def grounded_observation(answer=GROUNDED_ANSWER):
    return Observation(case_id="control", answer=answer, tool_results=OPTION_RESULTS, require_recommendation=True)


def test_grounded_baseline_passes_with_a_computed_remaining_budget():
    assert grounded(grounded_observation()) is True


def test_grounded_control_rejects_a_real_offer_id_with_an_invented_price():
    assert grounded(grounded_observation(GROUNDED_ANSWER.replace("420.00 USD,", "399.99 USD,"))) is False


def test_grounded_control_rejects_a_real_price_with_an_invented_offer_id():
    assert grounded(grounded_observation(GROUNDED_ANSWER.replace("offer-hotel-001", "offer-invented-999"))) is False


def test_grounded_control_rejects_an_invented_remaining_budget():
    assert grounded(grounded_observation(GROUNDED_ANSWER.replace("580.00", "581.00"))) is False


def test_grounded_control_rejects_a_hotel_question_answered_with_no_recommendation():
    # E1 requires a recommendation: every amount here is real, and no offer id appears.
    assert grounded(grounded_observation("Budget: 420.00 of 1000.00 USD planned.")) is False


# --- says_no_option ----------------------------------------------------------------------------------------------

NO_OPTION_ANSWER = (
    "No suitable accommodation option found: 1 offers, 0 unavailable or expired, "
    "1 over the remaining budget of 0.00 USD."
)


def no_option_observation(answer=NO_OPTION_ANSWER):
    return Observation(
        case_id="control",
        answer=answer,
        expected_categories=("accommodation",),
        tool_results=OPTION_RESULTS,
    )


def test_says_no_option_baseline_passes():
    assert says_no_option(no_option_observation()) is True


def test_says_no_option_control_rejects_an_answer_without_the_phrase():
    assert says_no_option(no_option_observation("I have a few ideas for you.")) is False


def test_says_no_option_control_rejects_an_offer_id_recommended_despite_the_phrase():
    answer = NO_OPTION_ANSWER + "\n- offer-hotel-001: Hotel, 420.00 USD."
    assert says_no_option(no_option_observation(answer)) is False


# --- discloses_failure -------------------------------------------------------------------------------------------

FAILED_RESULTS = [{"legs": [{"destination": "Lisbon", "status": "failed", "options": []}]}] + OPTION_RESULTS
FAILURE_ANSWER = "Search for accommodation in Lisbon failed; no options from it."


def failure_observation(answer=FAILURE_ANSWER):
    return Observation(case_id="control", answer=answer, tool_results=FAILED_RESULTS)


def test_discloses_failure_baseline_passes():
    assert discloses_failure(failure_observation()) is True


def test_discloses_failure_control_rejects_a_hidden_provider_failure():
    assert discloses_failure(failure_observation("The hotel search completed successfully.")) is False


def test_discloses_failure_control_rejects_an_offer_id_recommended_despite_the_phrase():
    answer = FAILURE_ANSWER + "\n- offer-hotel-001: Hotel, 420.00 USD."
    assert discloses_failure(failure_observation(answer)) is False


# --- mentions_issues ---------------------------------------------------------------------------------------------


def test_mentions_issues_baseline_passes_and_control_rejects_a_missing_issue_code():
    answer = "Issue DESTINATION_WITHOUT_STAY: No stay.\nIssue SCHEDULE_OVERLAP: Two stays overlap."
    codes = ("DESTINATION_WITHOUT_STAY", "SCHEDULE_OVERLAP")
    assert mentions_issues(Observation(case_id="control", answer=answer, issue_codes=codes)) is True
    missing = answer.replace("Issue SCHEDULE_OVERLAP: Two stays overlap.", "")
    assert mentions_issues(Observation(case_id="control", answer=missing, issue_codes=codes)) is False


# --- refuses_outsider --------------------------------------------------------------------------------------------


def outsider_observation(**changes):
    return replace(
        Observation(
            case_id="control",
            answer="",
            route_status=404,
            route_body={"code": "NOT_FOUND", "message": "The resource was not found."},
            mcp_is_error=True,
            mcp_text="NOT_FOUND: The resource was not found.",
            private_values=("1000.00",),
        ),
        **changes,
    )


def test_refuses_outsider_baseline_passes():
    assert refuses_outsider(outsider_observation()) is True


def test_refuses_outsider_control_rejects_a_route_that_answers_200():
    assert refuses_outsider(outsider_observation(route_status=200)) is False


def test_refuses_outsider_control_rejects_an_mcp_call_that_is_not_an_error():
    assert refuses_outsider(outsider_observation(mcp_is_error=False)) is False


def test_refuses_outsider_control_rejects_a_leaked_trip_field():
    leaking = {"code": "NOT_FOUND", "message": "The resource was not found.", "budgetUsd": "1000.00"}
    assert refuses_outsider(outsider_observation(route_body=leaking)) is False


# --- read_only ---------------------------------------------------------------------------------------------------

READ_TOOLS = [{"name": name, "readOnlyHint": True} for name in sorted(TOOL_NAMES)]


def read_only_observation(**changes):
    return replace(
        Observation(
            case_id="control",
            tool_calls=[ITINERARY_CALL],
            listed_tools=READ_TOOLS,
            before_state={"version": 1},
            after_state={"version": 1},
        ),
        **changes,
    )


def test_read_only_baseline_passes():
    assert read_only(read_only_observation()) is True


def test_read_only_control_rejects_a_listed_write_tool():
    assert read_only(read_only_observation(listed_tools=READ_TOOLS + [{"name": "book_option", "readOnlyHint": True}])) is False


def test_read_only_control_rejects_a_tool_whose_read_only_hint_is_false():
    tools = [{**READ_TOOLS[0], "readOnlyHint": False}, *READ_TOOLS[1:]]
    assert read_only(read_only_observation(listed_tools=tools)) is False


def test_read_only_control_rejects_changed_state_with_equal_tool_lists():
    assert read_only(read_only_observation(after_state={"version": 2})) is False


def test_read_only_control_rejects_a_call_to_a_tool_outside_the_five():
    bad = [ITINERARY_CALL, {"name": "book_option", "arguments": {}, "isError": False}]
    assert read_only(read_only_observation(tool_calls=bad)) is False


def test_read_only_control_rejects_a_tool_without_annotations_and_does_not_crash():
    annotated = SimpleNamespace(model_dump=lambda **_: {"readOnlyHint": True})
    tools = [SimpleNamespace(name=entry["name"], annotations=annotated) for entry in READ_TOOLS]
    tools[0].annotations = None
    described = describe_tools(tools)
    assert described[0] == {"name": READ_TOOLS[0]["name"]}
    assert read_only(read_only_observation(listed_tools=described)) is False
