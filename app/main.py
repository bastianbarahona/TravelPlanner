"""Travel planner API. Every error leaves as the provider's Error shape: {code, message, requestId}
plus optional details. FastAPI's default {"detail": ...} bodies never appear."""
import logging
from http import HTTPStatus
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api import participants, trips
from app.domain.errors import ApiError, not_found

logger = logging.getLogger("uvicorn.error")
BAD_JSON = "The request body is not valid JSON."

# docs_url and redoc_url off: they are routes outside the schema, and /openapi.json must list every route.
# redirect_slashes off, as in the mock: a trailing slash must not become an undeclared 307.
app = FastAPI(title="Travel Planner", docs_url=None, redoc_url=None, redirect_slashes=False)
app.include_router(trips.collection)
app.include_router(trips.readers)
app.include_router(trips.organizers)
app.include_router(participants.router)


@app.get("/health")
def health():
    # ponytail: liveness only, no database query. Add a `SELECT 1` if compose should wait on Postgres reachability too.
    return {"status": "ok"}


def error_response(status, code, message, details=None, headers=None):
    request_id = f"request-{uuid4().hex}"
    logger.warning("error status=%s code=%s requestId=%s", status, code, request_id)
    body = {"code": code, "message": message, "requestId": request_id}
    if details is not None:
        body["details"] = details
    return JSONResponse(body, status_code=status, headers=headers)


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
