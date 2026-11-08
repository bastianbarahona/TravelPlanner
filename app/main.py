"""Travel planner API. Every error leaves as the provider's Error shape: {code, message, requestId}
plus optional details. FastAPI's default {"detail": ...} bodies never appear."""
import asyncio
import logging
from contextlib import asynccontextmanager
from http import HTTPStatus
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException

from app import config
from app.api import assistant, documents, itinerary, participants, search, trips
from app.api.deps import bearer_user, unauthorized
from app.context import request_id
from app.db.session import engine
from app.domain.errors import ApiError, not_found
from app.mcp.server import server as mcp_server
from app.services import booking
from app.services.documents import document_worker

logger = logging.getLogger("uvicorn.error")
BAD_JSON = "The request body is not valid JSON."


@asynccontextmanager
async def lifespan(_app):
    """Two background loops, each its own function and each behind its own flag: none calls the other. Both resume from
    the database. Booking reconciliation only reads from the provider (CONTRACT_NOTES 2.9); documents only render."""
    tasks = []
    if config.BOOKING_POLL_ENABLED:
        tasks.append(asyncio.create_task(booking.reconcile_forever()))
    if config.DOCUMENT_WORKER_ENABLED:
        tasks.append(asyncio.create_task(document_worker()))
    try:
        async with mcp_server.session_manager.run():  # the /mcp app below cannot serve before it runs
            yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# docs_url and redoc_url off: they are routes outside the schema, and /openapi.json must list every route.
# redirect_slashes off, as in the mock: a trailing slash must not become an undeclared 307.
app = FastAPI(title="Travel Planner", docs_url=None, redoc_url=None, redirect_slashes=False, lifespan=lifespan)
app.include_router(trips.collection)
app.include_router(trips.readers)
app.include_router(trips.organizers)
app.include_router(participants.router)
app.include_router(itinerary.readers)
app.include_router(itinerary.editors)
app.include_router(search.router)
app.include_router(documents.readers)
app.include_router(documents.editors)
app.include_router(assistant.router)


class RequestIdMiddleware:
    """Pure ASGI, so the id is set in the task that runs the route and its error handlers. Every response carries it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        identifier = f"request-{uuid4().hex}"
        request_id.set(identifier)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).setdefault("X-Request-Id", identifier)
            await send(message)

        await self.app(scope, receive, send_with_id)


app.add_middleware(RequestIdMiddleware)


@app.get("/health")
def health():
    # Liveness only: the process answers. /ready checks the database, and the compose healthcheck uses it.
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: the database answers. The provider is never called, because the application serves without it."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return error_response(503, "NOT_READY", "The database is not reachable. Try again shortly.")
    return {"status": "ready"}


def error_response(status, code, message, details=None, headers=None):
    identifier = request_id.get() or f"request-{uuid4().hex}"
    logger.warning("error status=%s code=%s requestId=%s", status, code, identifier)
    body = {"code": code, "message": message, "requestId": identifier}
    if details is not None:
        body["details"] = details
    # The header is set here too: a 500 leaves through the server's outermost handler, past the middleware's send.
    return JSONResponse(body, status_code=status, headers={**(headers or {}), "X-Request-Id": identifier})


@app.exception_handler(ApiError)
async def on_api_error(_request, exc):
    return error_response(exc.status, exc.code, exc.message, exc.details, exc.headers)


@app.exception_handler(HTTPException)
async def on_http_exception(_request, exc):
    # Starlette's own 404 (unknown route, or a path uvicorn decoded into extra segments such as %2F) and 405.
    status = HTTPStatus(exc.status_code)
    if status == 400:
        # FastAPI's 400 for a body it cannot read: undecodable JSON (invalid UTF-8, Python's int digit limit) answers like
        # malformed JSON; a client that disconnected mid-body gets none. The digit limit stays on: denial-of-service guard.
        return error_response(422, "VALIDATION_FAILED", "The request is invalid.", {"errors": [{"field": "body", "message": BAD_JSON}]})
    message = {404: not_found().message, 405: "This method is not allowed on this route."}.get(status, status.phrase)
    return error_response(status, status.phrase.upper().replace(" ", "_"), message, headers=exc.headers)


def _field(error):
    """Pydantic's error to the API's {field, message}. The input is never echoed."""
    location = error["loc"][1:]  # drop "body"
    message = error["msg"].removeprefix("Value error, ")
    match error["type"]:
        case "json_invalid":
            return "body", BAD_JSON
        case "missing" if not location:
            return "body", "A JSON object body is required."
        case "model_attributes_type":
            return "body", "The request body must be a JSON object."
        case "extra_forbidden":
            return "body", "The body has a field this request does not accept."
        case "missing":
            message = "This field is required."
        case "too_short":  # only the destinations list has a minimum
            message = "Add at least one destination."
    field = ""
    for part in location:
        field += f"[{part}]" if isinstance(part, int) else f".{part}" if field else part
    return field or "body", message


@app.exception_handler(RequestValidationError)
async def on_validation_error(_request, exc):
    errors = [{"field": field, "message": message} for field, message in map(_field, exc.errors())]
    return error_response(422, "VALIDATION_FAILED", "The request is invalid.", {"errors": errors})


@app.exception_handler(Exception)
async def on_unexpected_error(_request, exc):
    logger.error("unexpected error", exc_info=exc)
    return error_response(500, "INTERNAL_ERROR", "Something went wrong. Quote the requestId when you report it.")


class McpAuthGuard:
    """Refuses a missing or unknown bearer token with the API's own 401 before MCP runs: a Mount is outside every router's
    dependencies. The tools check the same token again."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and bearer_user(Headers(scope=scope).get("authorization", "")) is None:
            response = await on_api_error(None, unauthorized())
            return await response(scope, receive, send)
        await self.app(scope, receive, send)


async def _no_such_path(scope, receive, send):
    raise not_found()


# Mount("/mcp") matches /mcp/... only. The bare path gets the same guard, so a caller without a token is told 401 there too,
# and a valid one keeps the 404: the endpoint is /mcp/.
app.add_route("/mcp", McpAuthGuard(_no_such_path), include_in_schema=False)
# Stateless JSON-RPC over HTTP at /mcp/. The SDK's DNS-rebinding guard stays on: it admits loopback hosts only.
app.mount("/mcp", McpAuthGuard(mcp_server.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True)))
