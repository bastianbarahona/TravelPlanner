"""Iteration 6 requirements 1-5: authenticated, read-only MCP tools over real HTTP."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from app_support import (
    ProviderStub,
    TOKENS,
    add_participant,
    app_client as make_app_client,
    assert_error,
    check_app_response,
    create_planning_trip,
    planning_trip_body,
)
from booking_support import add_item, book_item, forward, get_itinerary, seed_booked_trip, state_snapshot
from support import HTTP_OPTIONS

TOOL_NAMES = {
    "list_trips",
    "get_trip",
    "get_itinerary",
    "list_documents",
    "search_options",
}


async def _list_tools(base_url, user="ana"):
    async with create_mcp_http_client(
        headers={"Authorization": f"Bearer {TOKENS[user]}"}
    ) as http:
        async with Client(
            streamable_http_client(f"{base_url}/mcp/", http_client=http), cache=None
        ) as client:
            return (await client.list_tools()).tools


async def _call_tool(base_url, user, name, arguments):
    async with create_mcp_http_client(
        headers={"Authorization": f"Bearer {TOKENS[user]}"}
    ) as http:
        async with Client(
            streamable_http_client(f"{base_url}/mcp/", http_client=http), cache=None
        ) as client:
            return await client.call_tool(name, arguments)


def mcp_tools(base_url, user="ana"):
    return asyncio.run(_list_tools(base_url, user))


def mcp_call(base_url, user, name, arguments=None):
    return asyncio.run(_call_tool(base_url, user, name, arguments or {}))


def structured(result):
    assert result.is_error is False, result.content
    assert isinstance(result.structured_content, dict), result.content
    return result.structured_content


def error_text(result, tool):
    """The tool's own `CODE: message`. The SDK puts `Error executing tool <name>: ` in front of every tool error."""
    assert result.is_error is True
    assert len(result.content) == 1 and isinstance(result.content[0].text, str)
    prefix = f"Error executing tool {tool}: "
    message = result.content[0].text
    assert message.startswith(prefix), message
    return message.removeprefix(prefix)


def without_clock_fields(view):
    """The itinerary's expiry hints use the application clock; the persisted fields do not."""
    value = deepcopy(view)
    for item in value["items"]:
        item.pop("expired", None)
        item.pop("bookable", None)
    return value


def trip_tool_arguments(name, trip_id):
    arguments = {"trip_id": trip_id}
    if name == "search_options":
        arguments["category"] = "accommodation"
    return arguments


TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.mark.parametrize("path", ["/mcp/", "/mcp"])
def test_mcp_without_a_token_or_with_a_wrong_one_is_401_in_the_error_shape(application, path):
    with httpx.Client(
        base_url=application.url,
        event_hooks={"response": [check_app_response]},
        **HTTP_OPTIONS,
    ) as anonymous:
        missing = anonymous.post(path, json=TOOLS_LIST, headers=MCP_HEADERS)
        wrong = anonymous.post(
            path, json=TOOLS_LIST, headers={**MCP_HEADERS, "Authorization": "Bearer wrong-token"}
        )
    for response in (missing, wrong):
        assert_error(response, 401, "UNAUTHORIZED")
        assert response.headers["www-authenticate"] == "Bearer"


def test_mcp_lists_exactly_five_read_only_tools(application, empty_database):
    tools = mcp_tools(application.url)
    assert {tool.name for tool in tools} == TOOL_NAMES
    assert len(tools) == len(TOOL_NAMES)
    for tool in tools:
        assert tool.annotations is not None, tool.name
        annotations = tool.annotations.model_dump(by_alias=True, exclude_none=True)
        assert annotations["readOnlyHint"] is True, (tool.name, annotations)


def create_trip_as(client, **overrides):
    """create_planning_trip asserts that the organizer is ana; these trips belong to whoever the client is."""
    response = client.post("/trips", json=planning_trip_body(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_trip_tools_match_routes_for_every_role_and_hide_every_kind_of_unknown_trip(
    application, app_client, control
):
    control.pin_clock("2030-01-15T12:00:00Z")
    trip = create_planning_trip(app_client)
    add_participant(app_client, trip["id"], "ben", "editor")
    add_participant(app_client, trip["id"], "cam", "viewer")
    # Real content, so no comparison below is between empty lists: a booked item, a plain one and a document.
    hotel = add_item(app_client, trip["id"], 1, "offer-hotel-001")[1]
    add_item(app_client, trip["id"], get_itinerary(app_client, trip["id"])["version"], "offer-activity-001")
    booked = book_item(app_client, trip["id"], hotel)
    assert booked.status_code == 201, booked.text
    assert app_client.post(f"/trips/{trip['id']}/documents").status_code == 202
    itinerary = get_itinerary(app_client, trip["id"])
    assert len(itinerary["items"]) == 2 and any("booking" in item for item in itinerary["items"])
    assert app_client.get(f"/trips/{trip['id']}/documents").json()["documents"]
    tools = ("get_trip", "get_itinerary", "list_documents", "search_options")

    for user in ("ana", "ben", "cam"):
        with make_app_client(application.url, user) as actor:
            routes = {
                "get_trip": actor.get(f"/trips/{trip['id']}").json(),
                "get_itinerary": actor.get(f"/trips/{trip['id']}/itinerary").json(),
                "list_documents": actor.get(f"/trips/{trip['id']}/documents").json(),
                "search_options": actor.post(
                    f"/trips/{trip['id']}/search", json={"category": "accommodation"}
                ).json(),
            }
        for name in tools:
            actual = structured(
                mcp_call(application.url, user, name, trip_tool_arguments(name, trip["id"]))
            )
            if name == "get_itinerary":
                assert without_clock_fields(actual) == without_clock_fields(routes[name])
            else:
                assert actual == routes[name]

    missing = str(uuid4())
    for name in tools:
        texts = [
            error_text(mcp_call(application.url, "dee", name, trip_tool_arguments(name, value)), name)
            for value in (trip["id"], missing, "not-a-uuid")
        ]
        assert len(set(texts)) == 1, (name, texts)
        assert texts[0].startswith("NOT_FOUND:")

    with make_app_client(application.url, "ben") as ben:
        bens_trip = create_trip_as(ben, destinations=["Porto"])
    for name in tools:
        hidden = error_text(
            mcp_call(application.url, "cam", name, trip_tool_arguments(name, bens_trip["id"])), name
        )
        assert hidden.startswith("NOT_FOUND:")
        assert bens_trip["id"] not in hidden and "Porto" not in hidden


def test_list_trips_returns_only_the_callers_memberships(application, app_client):
    shared = create_planning_trip(app_client, destinations=["Lisbon"])
    add_participant(app_client, shared["id"], "ben", "viewer")
    private = create_planning_trip(app_client, destinations=["Faro"])
    with make_app_client(application.url, "ben") as ben:
        own = create_trip_as(ben, destinations=["Porto"])
        expected = ben.get("/trips").json()

    result = structured(mcp_call(application.url, "ben", "list_trips"))
    assert result == expected
    assert {trip["id"] for trip in result["trips"]} == {shared["id"], own["id"]}
    assert private["id"] not in {trip["id"] for trip in result["trips"]}


def test_calling_all_five_tools_changes_no_state_and_never_books(start_app, mock, control, db_engine):
    # ponytail: ProviderStub is only an observable proxy here; the real mock control API has no request log.
    with ProviderStub(lambda request: forward(mock.url, request)) as proxy:
        process = start_app(
            PROVIDER_BASE_URL=proxy.url, DOCUMENT_WORKER_ENABLED="0", BOOKING_POLL_ENABLED="0"
        )
        with make_app_client(process.url) as client:
            trip = seed_booked_trip(client)
            before = state_snapshot(client, trip["id"], db_engine)
            proxy.requests.clear()

            structured(mcp_call(process.url, "ana", "list_trips"))
            for name in ("get_trip", "get_itinerary", "list_documents", "search_options"):
                structured(mcp_call(process.url, "ana", name, trip_tool_arguments(name, trip["id"])))

            after = state_snapshot(client, trip["id"], db_engine)

    assert after == before
    assert any(request["path"] == "/v1/offers/search" for request in proxy.requests)
    assert not any(request["path"] == "/v1/bookings" for request in proxy.requests)


def test_search_options_with_the_provider_down_returns_failed_legs_not_an_error(
    application, app_client, control
):
    trip = create_planning_trip(app_client, destinations=["Lisbon", "Porto"])
    control.fault(503)
    result = structured(
        mcp_call(
            application.url, "ana", "search_options", {"trip_id": trip["id"], "category": "accommodation"}
        )
    )
    assert [leg["destination"] for leg in result["legs"]] == ["Lisbon", "Porto"]
    assert {leg["status"] for leg in result["legs"]} == {"failed"}
    assert all(leg["options"] == [] and leg["error"] for leg in result["legs"])


def test_search_options_with_an_invalid_category_names_the_field_and_never_the_input(
    application, app_client
):
    trip = create_planning_trip(app_client)
    sent = "secret-invalid-category"
    message = error_text(
        mcp_call(application.url, "ana", "search_options", {"trip_id": trip["id"], "category": sent}),
        "search_options",
    )
    assert message.startswith("VALIDATION_FAILED: "), message
    assert "category" in message.lower()
    assert sent not in message


MARKER = "marker-secret-value-4417"


@pytest.mark.parametrize(
    ("tool", "arguments", "argument"),
    [
        ("search_options", lambda trip_id: {"trip_id": trip_id, "category": [MARKER]}, "category"),
        ("search_options", lambda trip_id: {"trip_id": trip_id}, "category"),
        (
            "search_options",
            lambda trip_id: {"trip_id": trip_id, "category": "accommodation", "unexpected_arg": MARKER},
            "unexpected_arg",
        ),
        ("get_trip", lambda trip_id: {"trip_id": trip_id, "unexpected_arg": MARKER}, "unexpected_arg"),
    ],
    ids=["wrong-type", "missing", "unknown-search-argument", "unknown-get-trip-argument"],
)
def test_every_tool_fails_a_bad_argument_as_validation_failed_naming_it_and_never_the_input(
    application, app_client, tool, arguments, argument
):
    trip = create_planning_trip(app_client)
    result = mcp_call(application.url, "ana", tool, arguments(trip["id"]))
    message = error_text(result, tool)
    assert message.startswith("VALIDATION_FAILED: "), message
    assert argument in message, message
    assert MARKER not in result.model_dump_json()
    assert "input_value" not in message and "validation error for" not in message, message
