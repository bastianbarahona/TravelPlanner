"""The read-only MCP server: five tools, each calling the same service function as its route.

No tool takes a user. The caller is the bearer token of the HTTP request that carries the call, read from the request
headers, and every tool that takes a trip resolves membership through `membership`, the function `trip_member` calls.
Mounted at /mcp by app/main.py, behind a guard that refuses a missing or unknown token before MCP runs.
"""
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi.concurrency import run_in_threadpool
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError
from pydantic.alias_generators import to_snake
from sqlalchemy.orm import Session

from app.api.deps import bearer_user, membership, unauthorized
from app.api.schemas import SearchTrip
from app.db.session import engine, get_session
from app.domain.errors import ApiError, ValidationFailed
from app.services import documents, itinerary, search, trips



class ReadOnlyServer(MCPServer):
    async def call_tool(self, name, arguments, context=None):
        """The one boundary for every tool's arguments. The SDK checks them against the typed signature first and answers in
        its own text, which echoes the input, and it ignores unknown arguments. Here a wrong type, a missing argument and an
        unknown one all answer VALIDATION_FAILED, field names and messages only, and the schema the model sees is unchanged."""
        # ponytail: _tool_manager is private in mcp 2.3.0 (pinned): look it up again when the pin moves.
        tool = self._tool_manager.get_tool(name)
        if tool is not None:
            problems = [
                {"field": field, "message": "This argument is not accepted."}
                for field in sorted(set(arguments) - set(tool.parameters.get("properties", {})))
            ]
            try:
                tool.fn_metadata.validate_arguments(arguments)
            except ValidationError as error:  # `msg` never holds the input: `input` is not read
                problems += [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in error.errors()]
            if problems:
                raise ToolError(f"Error executing tool {name}: {_tool_error(ValidationFailed(problems))}")
        return await super().call_tool(name, arguments, context)


server = ReadOnlyServer("travel-planner")
READ_ONLY = ToolAnnotations(read_only_hint=True)
session_scope = contextmanager(get_session)  # the route's own session handling


def _caller(ctx):
    user = bearer_user((ctx.headers or {}).get("authorization", ""))
    if user is None:  # the guard in front of /mcp already refused this
        raise _tool_error(unauthorized())
    return user


def _tool_error(error):
    if error.code == "VALIDATION_FAILED":  # the field names, in the tool's own argument names, and the messages
        fields = "; ".join(f"{to_snake(e['field'])}: {e['message']}" for e in error.details["errors"])
        return ToolError(f"VALIDATION_FAILED: {fields}")
    return ToolError(f"{error.code}: {error.message}")


@contextmanager
def _refusals():
    """An ApiError reaches the client as the tool's error text. Any other exception stays the SDK's generic one."""
    try:
        yield
    except ApiError as error:
        raise _tool_error(error) from None


def _read(ctx, trip_id, use):
    user = _caller(ctx)
    with _refusals(), session_scope() as session:
        return use(session, membership(session, user, trip_id))


@server.tool(annotations=READ_ONLY)
def list_trips(ctx: Context) -> dict[str, Any]:
    """List the trips the caller takes part in."""
    user = _caller(ctx)
    with _refusals(), session_scope() as session:
        return {"trips": trips.list_trips(session, user)}


@server.tool(annotations=READ_ONLY)
def get_trip(ctx: Context, trip_id: str) -> dict[str, Any]:
    """Read one trip: destinations, dates, budget, travelers, version and participants."""
    return _read(ctx, trip_id, lambda session, access: trips.read_trip(session, access.user, access.trip_id))


@server.tool(annotations=READ_ONLY)
def get_itinerary(ctx: Context, trip_id: str) -> dict[str, Any]:
    """Read a trip's itinerary: items with their booking states, issues, the rules and the budget."""
    return _read(ctx, trip_id, lambda session, access: itinerary.itinerary_view(session, access.user, access.trip_id))


@server.tool(annotations=READ_ONLY)
def list_documents(ctx: Context, trip_id: str) -> dict[str, Any]:
    """List a trip's documents."""
    return _read(ctx, trip_id, lambda session, access: documents.list_documents(session, access))


def _invalid(error):
    """pydantic's errors as the API's {field, message}. Its `input` is never read, so no input is echoed."""
    return ValidationFailed(
        [{"field": ".".join(map(str, e["loc"])), "message": e["msg"].removeprefix("Value error, ")} for e in error.errors()]
    )


@server.tool(annotations=READ_ONLY)
async def search_options(
    ctx: Context,
    trip_id: str,
    category: Annotated[str, Field(description="transport, accommodation or activity")],
    origin: Annotated[str | None, Field(description="Required for transport")] = None,
    destinations: Annotated[list[str] | None, Field(description="Defaults to the trip's destinations")] = None,
    starts_on: Annotated[str | None, Field(description="YYYY-MM-DD. Defaults to the trip's start")] = None,
    ends_on: Annotated[str | None, Field(description="YYYY-MM-DD. Defaults to the trip's end")] = None,
    provider_id: Annotated[str | None, Field(description="Search one provider only")] = None,
) -> dict[str, Any]:
    """Search the provider for options (offers with prices and availability) for a trip. Nothing is booked or saved."""
    user = _caller(ctx)
    sent = {"category": category, "origin": origin, "destinations": destinations, "startsOn": starts_on,
            "endsOn": ends_on, "providerId": provider_id}
    session = Session(engine)
    try:  # database work in the thread pool, no transaction open while the provider answers: as search_trip does
        with _refusals():
            access = await run_in_threadpool(membership, session, user, trip_id)
            try:
                body = SearchTrip.model_validate({key: value for key, value in sent.items() if value is not None})
            except ValidationError as error:
                raise _invalid(error) from None
            return await search.search_trip(session, access, body)
    finally:
        await run_in_threadpool(session.close)
