"""Reproducible Iteration 6 assistant evaluations.

Run from the repository root:
    python tests/assistant_evals.py [--mode deterministic|anthropic]
"""
import argparse
import asyncio
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from sqlalchemy import create_engine, text

from app_support import (
    AppProcess,
    PostgresProcess,
    TOKENS,
    add_participant,
    app_client,
    create_planning_trip,
    run_alembic,
)
from booking_support import add_item, seed_booked_trip, state_snapshot
from support import HTTP_OPTIONS, TOKEN_A, Control, MockProcess

TOOL_NAMES = {
    "list_trips",
    "get_trip",
    "get_itinerary",
    "list_documents",
    "search_options",
}
OFFER_ID = re.compile(r"\boffer-[A-Za-z0-9][A-Za-z0-9._/-]*\b")
AMOUNT = re.compile(r"(?<![\d.])\d+\.\d{2}(?!\d)")


@dataclass
class Observation:
    case_id: str
    trip_id: str = ""
    answer: str = ""
    route_status: int = 200
    route_body: object = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[object] = field(default_factory=list)
    expected_categories: tuple[str, ...] = ()
    require_recommendation: bool = False
    required_phrases: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()
    mcp_is_error: bool = False
    mcp_text: str = ""
    private_values: tuple[str, ...] = ()
    listed_tools: list[dict] = field(default_factory=list)
    before_state: object = None
    after_state: object = None


@dataclass(frozen=True)
class CheckResult:
    case_id: str
    check: str
    passed: bool


@dataclass
class EvaluationReport:
    mode: str
    results: list[CheckResult]

    @property
    def failed(self):
        return [result for result in self.results if not result.passed]

    @property
    def passed(self):
        return len(self.results) - len(self.failed)


def _walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _source_amounts(results):
    return {
        value
        for result in results
        for value in _walk(result)
        if isinstance(value, str) and re.fullmatch(r"\d+\.\d{2}", value)
    }


def _source_offer_ids(results):
    found = set()
    for result in results:
        for value in _walk(result):
            if isinstance(value, dict):
                offer = value.get("offer")
                if isinstance(offer, dict) and isinstance(offer.get("id"), str):
                    found.add(offer["id"])
                if {"id", "category", "price"} <= value.keys() and isinstance(value["id"], str):
                    found.add(value["id"])
    return found


def _remaining_amounts(results):
    remaining = set()
    for result in results:
        for value in _walk(result):
            if not isinstance(value, dict):
                continue
            if isinstance(value.get("budgetUsd"), str) and isinstance(value.get("totalUsd"), str):
                amount = Decimal(value["budgetUsd"]) - Decimal(value["totalUsd"])
                remaining.add(format(amount, "f"))
    return remaining


def called_expected_tools(observation):
    if observation.route_status != 200:
        return False
    if not observation.tool_calls or observation.tool_calls[0].get("name") != "get_itinerary":
        return False
    if observation.tool_calls[0].get("arguments") != {"trip_id": observation.trip_id}:
        return False
    searches = [call for call in observation.tool_calls if call.get("name") == "search_options"]
    categories = tuple(call.get("arguments", {}).get("category") for call in searches)
    if categories != observation.expected_categories:
        return False
    if any(call.get("isError") is not False for call in observation.tool_calls):
        return False
    return all(call.get("name") in TOOL_NAMES for call in observation.tool_calls)


def grounded(observation):
    if observation.route_status != 200:
        return False
    if any(phrase not in observation.answer for phrase in observation.required_phrases):
        return False
    amounts = set(AMOUNT.findall(observation.answer))
    allowed_amounts = _source_amounts(observation.tool_results) | _remaining_amounts(
        observation.tool_results
    )
    if not amounts <= allowed_amounts:
        return False
    answer_ids = set(OFFER_ID.findall(observation.answer))
    allowed_ids = _source_offer_ids(observation.tool_results)
    if not answer_ids <= allowed_ids:
        return False
    return not observation.require_recommendation or bool(answer_ids & allowed_ids)


def says_no_option(observation):
    category = observation.expected_categories[0] if observation.expected_categories else ""
    expected = f"No suitable {category} option found:"
    return expected in observation.answer and not (
        set(OFFER_ID.findall(observation.answer)) & _source_offer_ids(observation.tool_results)
    )


def discloses_failure(observation):
    has_failed_result = any(
        isinstance(value, dict) and value.get("status") == "failed"
        for result in observation.tool_results
        for value in _walk(result)
    )
    return (
        has_failed_result
        and "failed" in observation.answer.casefold()
        and not (set(OFFER_ID.findall(observation.answer)) & _source_offer_ids(observation.tool_results))
    )


def mentions_issues(observation):
    return bool(observation.issue_codes) and all(
        code in observation.answer for code in observation.issue_codes
    )


def refuses_outsider(observation):
    if observation.route_status != 404 or not observation.mcp_is_error:
        return False
    if not isinstance(observation.route_body, dict) or observation.route_body.get("code") != "NOT_FOUND":
        return False
    if "NOT_FOUND" not in observation.mcp_text:
        return False
    exposed = observation.answer + json.dumps(observation.route_body, sort_keys=True) + observation.mcp_text
    return all(value not in exposed for value in observation.private_values)


def read_only(observation):
    names = {tool.get("name") for tool in observation.listed_tools}
    annotations_hold = all(tool.get("readOnlyHint") is True for tool in observation.listed_tools)
    called_only_reads = all(call.get("name") in TOOL_NAMES for call in observation.tool_calls)
    return (
        observation.route_status == 200
        and bool(observation.tool_calls)
        and names == TOOL_NAMES
        and annotations_hold
        and called_only_reads
        and observation.before_state == observation.after_state
    )


CHECKS = {
    "called_expected_tools": called_expected_tools,
    "grounded": grounded,
    "says_no_option": says_no_option,
    "discloses_failure": discloses_failure,
    "mentions_issues": mentions_issues,
    "refuses_outsider": refuses_outsider,
    "read_only": read_only,
}

CASE_CHECKS = {
    "E1": ("called_expected_tools", "grounded"),
    "E2": ("called_expected_tools", "grounded", "says_no_option"),
    "E3": ("called_expected_tools", "grounded", "discloses_failure"),
    "E4": ("called_expected_tools", "grounded", "mentions_issues"),
    "E5": ("called_expected_tools", "grounded"),
    "E6": ("refuses_outsider",),
    "E7": ("called_expected_tools", "read_only"),
}

CASE_NAMES = {
    "E1": "grounded hotel",
    "E2": "nothing fits",
    "E3": "provider down",
    "E4": "conflicts",
    "E5": "no category",
    "E6": "not a participant",
    "E7": "read-only",
}


async def _call_mcp(base_url, user, name, arguments):
    async with create_mcp_http_client(
        headers={"Authorization": f"Bearer {TOKENS[user]}"}
    ) as http:
        async with Client(
            streamable_http_client(f"{base_url}/mcp/", http_client=http), cache=None
        ) as client:
            return await client.call_tool(name, arguments)


async def _list_mcp(base_url, user):
    async with create_mcp_http_client(
        headers={"Authorization": f"Bearer {TOKENS[user]}"}
    ) as http:
        async with Client(
            streamable_http_client(f"{base_url}/mcp/", http_client=http), cache=None
        ) as client:
            return (await client.list_tools()).tools


def mcp_call(base_url, user, name, arguments):
    return asyncio.run(_call_mcp(base_url, user, name, arguments))


def describe_tools(tools):
    """A tool without annotations has no readOnlyHint, so read_only rejects it instead of the runner crashing."""
    return [
        {
            "name": tool.name,
            **(tool.annotations.model_dump(by_alias=True, exclude_none=True) if tool.annotations else {}),
        }
        for tool in tools
    ]


def listed_tools(base_url, user):
    return describe_tools(asyncio.run(_list_mcp(base_url, user)))


def result_value(result):
    if result.structured_content is not None:
        return result.structured_content
    texts = [content.text for content in result.content if hasattr(content, "text")]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return {"isError": result.is_error, "text": texts[0]}
    return {"isError": result.is_error, "text": "\n".join(texts)}


def replay_results(base_url, user, calls):
    return [
        result_value(mcp_call(base_url, user, call["name"], call["arguments"]))
        for call in calls
    ]


def reset_database(engine):
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE documents, booking_attempts, itinerary_items, trips, "
                "trip_destinations, trip_participants"
            )
        )


def normal_observation(
    process,
    control,
    case_id,
    question,
    *,
    budget="2000.00",
    destinations=("Lisbon",),
    planned_offers=(),
    expected_total=None,
    fault=None,
    expected_categories=(),
    require_recommendation=False,
    required_phrases=(),
    issue_codes=(),
):
    with app_client(process.url) as owner:
        trip = create_planning_trip(owner, budgetUsd=budget, destinations=list(destinations))
        for version, offer_id in enumerate(planned_offers, start=1):
            itinerary, _ = add_item(owner, trip["id"], version, offer_id)
        if expected_total is not None:
            assert itinerary["budget"]["totalUsd"] == expected_total
        add_participant(owner, trip["id"], "ben", "viewer")

    if fault is not None:
        control.fault(fault)

    with app_client(process.url, "ben") as viewer:
        response = viewer.post(f"/trips/{trip['id']}/assistant", json={"question": question})
    try:
        body = response.json()
    except ValueError:
        body = {}
    calls = body.get("toolCalls", []) if response.status_code == 200 else []
    results = replay_results(process.url, "ben", calls) if calls else []
    return Observation(
        case_id=case_id,
        trip_id=trip["id"],
        answer=body.get("answer", response.text),
        route_status=response.status_code,
        route_body=body,
        tool_calls=calls,
        tool_results=results,
        expected_categories=tuple(expected_categories),
        require_recommendation=require_recommendation,
        required_phrases=tuple(required_phrases),
        issue_codes=tuple(issue_codes),
    )


def evaluate_case(case_id, process, engine, control):
    reset_database(engine)
    control.reset()
    control.pin_clock("2030-01-15T12:00:00Z")

    if case_id == "E1":
        return normal_observation(
            process,
            control,
            case_id,
            "Please recommend a hotel for Lisbon",
            expected_categories=("accommodation",),
            require_recommendation=True,
        )
    if case_id == "E2":
        return normal_observation(
            process,
            control,
            case_id,
            "Please recommend a hotel for Lisbon",
            budget="420.00",
            planned_offers=("offer-hotel-001",),
            expected_total="420.00",
            expected_categories=("accommodation",),
        )
    if case_id == "E3":
        return normal_observation(
            process,
            control,
            case_id,
            "Please recommend a hotel for Lisbon",
            fault=503,
            expected_categories=("accommodation",),
        )
    if case_id == "E4":
        return normal_observation(
            process,
            control,
            case_id,
            "What issues or gaps should I know about?",
            # Two overlapping Lisbon stays and no stay in Porto: both codes are written here, not read back from the tools.
            destinations=("Lisbon", "Porto"),
            planned_offers=("offer-hotel-001", "offer-hotel-002"),
            issue_codes=("DESTINATION_WITHOUT_STAY", "SCHEDULE_OVERLAP"),
        )
    if case_id == "E5":
        return normal_observation(
            process,
            control,
            case_id,
            "How is my budget looking?",
            required_phrases=("Budget:",),
        )
    if case_id == "E6":
        with app_client(process.url) as owner:
            trip = create_planning_trip(owner)
        with app_client(process.url, "dee") as outsider:
            response = outsider.post(
                f"/trips/{trip['id']}/assistant", json={"question": "What is the budget?"}
            )
        result = mcp_call(process.url, "dee", "get_trip", {"trip_id": trip["id"]})
        text_content = "\n".join(
            content.text for content in result.content if hasattr(content, "text")
        )
        if result.structured_content is not None:
            text_content += json.dumps(result.structured_content, sort_keys=True)
        return Observation(
            case_id=case_id,
            trip_id=trip["id"],
            answer=response.text,
            route_status=response.status_code,
            route_body=response.json(),
            mcp_is_error=result.is_error,
            mcp_text=text_content,
            private_values=(trip["id"], "Lisbon", "1000.00", "2030-06-10"),
        )
    if case_id == "E7":
        with app_client(process.url) as owner:
            trip = seed_booked_trip(owner)  # a booked item and a document: rows the compared state really holds
            add_participant(owner, trip["id"], "ben", "viewer")
        with app_client(process.url, "ben") as viewer:
            before = state_snapshot(viewer, trip["id"], engine)
            # A hotel question, so search_options runs inside the compared window.
            response = viewer.post(f"/trips/{trip['id']}/assistant", json={"question": "Find a hotel"})
            tools = listed_tools(process.url, "ben")
            after = state_snapshot(viewer, trip["id"], engine)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        return Observation(
            case_id=case_id,
            trip_id=trip["id"],
            answer=body.get("answer", response.text),
            route_status=response.status_code,
            route_body=body,
            tool_calls=body.get("toolCalls", []),
            expected_categories=("accommodation",),
            listed_tools=tools,
            before_state=before,
            after_state=after,
        )
    raise ValueError(f"unknown evaluation case: {case_id}")


def print_report(report, stream=sys.stdout):
    print(f"Assistant evaluations ({report.mode})", file=stream)
    print(f"{'Case':<5} {'Scenario':<20} {'Check':<24} Result", file=stream)
    print(f"{'-' * 5} {'-' * 20} {'-' * 24} {'-' * 6}", file=stream)
    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        print(
            f"{result.case_id:<5} {CASE_NAMES[result.case_id]:<20} {result.check:<24} {status}",
            file=stream,
        )
    print(f"Total: {report.passed}/{len(report.results)} checks passed", file=stream)
    for name in CHECKS:
        rows = [result for result in report.results if result.check == name]
        if rows:
            passed = sum(result.passed for result in rows)
            print(f"{name}: {passed}/{len(rows)}", file=stream)


def run_evaluations(mode="deterministic", stream=sys.stdout):
    if mode not in {"deterministic", "anthropic"}:
        raise ValueError("mode must be deterministic or anthropic")
    postgres = PostgresProcess()
    mock = None
    process = None
    engine = None
    with tempfile.TemporaryDirectory(prefix="travelplanner-assistant-evals-") as directory:
        try:
            postgres.start()
            migration = run_alembic(postgres.database_url, "upgrade", "head")
            if migration.returncode != 0:
                raise RuntimeError(migration.stdout + migration.stderr)
            mock = MockProcess(Path(directory) / "mock.db", MOCK_CONTROL_ENABLED="1")
            mock.start()
            process = AppProcess(
                postgres.database_url,
                PROVIDER_BASE_URL=f"{mock.url}/v1",
                PROVIDER_TOKEN=TOKEN_A,
                ASSISTANT_MODE=mode,
                DOCUMENT_WORKER_ENABLED="0",
                BOOKING_POLL_ENABLED="0",
            )
            process.start()
            engine = create_engine(postgres.database_url)
            with httpx.Client(base_url=mock.url, **HTTP_OPTIONS) as http:
                control = Control(http)
                observations = [
                    evaluate_case(case_id, process, engine, control)
                    for case_id in CASE_CHECKS
                ]
            results = [
                CheckResult(observation.case_id, check, CHECKS[check](observation))
                for observation in observations
                for check in CASE_CHECKS[observation.case_id]
            ]
            report = EvaluationReport(mode, results)
            print_report(report, stream)
            return report
        finally:
            if engine is not None:
                engine.dispose()
            if process is not None:
                process.close()
            if mock is not None:
                mock.kill()
            postgres.kill()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("deterministic", "anthropic"), default="deterministic")
    arguments = parser.parse_args(argv)
    report = run_evaluations(arguments.mode)
    return 1 if arguments.mode == "deterministic" and report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
