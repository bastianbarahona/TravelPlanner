"""HTTP client for the external provider, bound to contracts/openAPI.json.

Responses are validated against the contract file itself (no second copy of the schemas), and what the schema
cannot say is checked on top: amounts through app/money.py, instants through parse_instant (CONTRACT_NOTES A44, A45).
The raw decoded JSON is what callers keep. A-numbers and section numbers refer to docs/CONTRACT_NOTES.md.
"""
import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote

import httpx
from jsonschema import Draft202012Validator, ValidationError

from app import config
from app.context import request_id
from app.domain.errors import ApiError
from app.domain.rules import parse_instant
from app.money import parse_amount

logger = logging.getLogger("uvicorn.error")

# app/providers/client.py -> the repository root (/app in the image), where the Dockerfile copies the contract.
CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "openAPI.json").read_text(encoding="utf-8"))
RETRY_AFTER = re.compile(r"[0-9]{1,4300}")
DEFAULT_RATE_LIMIT_WAIT = 5  # A40: when Retry-After is absent or invalid
MAX_WAIT = 5  # a longer wait is reported, not slept (A40)

# ponytail: one module-level client, never closed: the process exit closes its sockets. Add a lifespan hook if the
# application ever restarts the client in-process. trust_env off: no proxy or CA variables may redirect the provider.
http = httpx.AsyncClient(
    base_url=config.PROVIDER_BASE_URL,
    headers={"Authorization": f"Bearer {config.PROVIDER_TOKEN}"},
    timeout=httpx.Timeout(config.PROVIDER_TIMEOUT_SECONDS, connect=2),
    trust_env=False,
)


class ProviderError(ApiError):
    """A provider outcome the user must hear about. `status` is what add and refresh answer, and a search leg
    reports `code`, `message` and `retry_after` as its error."""

    def __init__(self, status, code, message, retry_after=None):
        known = retry_after is not None
        super().__init__(
            status,
            code,
            message,
            {"retryAfterSeconds": retry_after} if known else None,
            {"Retry-After": str(retry_after)} if known else None,
        )
        self.retry_after = retry_after


def _invalid(why, content=b""):
    # A42: the status and a bounded part of the body. Never a request header: ours carries the token.
    logger.error("provider response refused: %s body=%r", why[:200], content[:200])
    return ProviderError(502, "PROVIDER_INVALID_RESPONSE", "The provider sent a response this application cannot use.")


class Operation(NamedTuple):
    id: str  # the contract's operationId, for the log line
    validators: dict


class Reply(NamedTuple):
    """A declared status with a body that passed every check. `body` is a Booking or an Offer on 2xx, an Error otherwise."""

    status: int
    body: Any
    retry_after: int | None = None


def _operation(path, method):
    """The validator of every status the contract declares for one operation."""
    validators = {}
    for status, response in CONTRACT["paths"][path][method]["responses"].items():
        if "$ref" in response:
            response = CONTRACT["components"]["responses"][response["$ref"].rsplit("/", 1)[1]]
        schema = {**response["content"]["application/json"]["schema"], "components": CONTRACT["components"]}
        validators[int(status)] = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return Operation(CONTRACT["paths"][path][method]["operationId"], validators)


SEARCH = _operation("/offers/search", "post")
GET_OFFER = _operation("/offers/{offerId}", "get")
CREATE_BOOKING = _operation("/bookings", "post")
GET_BOOKING_BY_REFERENCE = _operation("/bookings/by-reference/{clientReference}", "get")


def _clean(value):
    """jsonb refuses a NUL, and a lone surrogate breaks JSON serialization, yet both are contract-valid strings.
    Every string of the body, keys included, is refused here, so nothing reaches the database or a response."""
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("a NUL character")
        value.encode("utf-8")  # a lone surrogate raises UnicodeEncodeError, a ValueError
    elif isinstance(value, list):
        for item in value:
            _clean(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _clean(key)
            _clean(item)


def _check_offer(offer):
    parse_amount(offer["price"]["amount"])  # the schema's `$` lets "420.00\n" through, and it has no upper bound
    for field in ("startsAt", "endsAt", "validUntil"):
        parse_instant(offer[field])


def _check_booking(reference):
    """`reference` is ours: another booking's reference is a provider bug, never adopted."""

    def check(booking):
        _check_offer(booking["offer"])
        parse_instant(booking["createdAt"])
        parse_instant(booking["updatedAt"])
        if booking["clientReference"] != reference:
            raise ValueError("clientReference is not ours")
        if (booking["status"] == "failed") != ("failure" in booking):  # A16 is prose only: trust `status`, say so, keep the booking
            logger.warning("provider booking %s breaks A16: status=%s, failure %s", booking["id"], booking["status"], "present" if "failure" in booking else "missing")

    return check


def _check_search(body):
    for offer in body["offers"]:
        _check_offer(offer)
    parse_instant(body["searchedAt"])


def _retry_after(response):
    text = response.headers.get("Retry-After", "")
    return int(text) if RETRY_AFTER.fullmatch(text) and int(text) >= 1 else None  # whole seconds, minimum 1


def _failure(status, error, retry_after):
    """A declared non-200 answer, already validated as the contract's Error."""
    match status:
        case 400:
            return ProviderError(
                400, "BAD_REQUEST", "The provider refused the search. Check the supplier (providerId) and the other fields."
            )
        case 401:
            # An operator problem: never told to the end user (A38, 3.2). _exchange has logged it as an ERROR.
            return ProviderError(502, "PROVIDER_AUTH", "The provider integration is misconfigured. Try again later.")
        case 404:
            return ProviderError(409, "OFFER_GONE", "The provider no longer has this offer.")
        case 429:
            return ProviderError(429, "RATE_LIMITED", "The provider is rate limiting us. Try again later.", retry_after)
        case _:  # 503 is the only other declared status
            return ProviderError(503, "PROVIDER_UNAVAILABLE", "The provider is temporarily unavailable. Try again later.")


async def _exchange(operation, check, deadline, method, path, **request):
    """One provider call and its one log line. A declared status with a valid body returns as a Reply. A call that
    cannot be read raises ProviderError: PROVIDER_UNREACHABLE (timeout, connection) or PROVIDER_INVALID_RESPONSE
    (undeclared status, non-JSON, schema-invalid). A body that passed the schema also passes `check`, on 2xx only."""
    outcome, provider_request_id = "unreachable", "-"
    try:
        try:
            # httpx's timeout applies to each read, so a response that drips bytes could run for ever. The deadline bounds the call.
            async with asyncio.timeout_at(deadline):
                response = await http.request(method, path, **request)
        except (httpx.HTTPError, TimeoutError) as error:
            if isinstance(error, (httpx.TimeoutException, TimeoutError)):
                outcome = "timeout"
            raise ProviderError(503, "PROVIDER_UNREACHABLE", "The provider did not answer in time. Try again later.") from None
        outcome = "invalid"
        validator = operation.validators.get(response.status_code)
        if validator is None:
            raise _invalid(f"undeclared status {response.status_code}", response.content)
        try:
            body = json.loads(response.content)
            _clean(body)
            validator.validate(body)
            if response.status_code < 300:
                check(body)
        except (ValueError, RecursionError, ValidationError) as error:  # ValueError: not JSON, amount, instant
            raise _invalid(f"status {response.status_code}: {error}", response.content) from None
        outcome = str(response.status_code)
        if response.status_code >= 400:
            provider_request_id = body["requestId"]
            if response.status_code == 401:  # an operator problem, whichever operation it was
                logger.error("provider rejected our token requestId=%s", provider_request_id)
        return Reply(response.status_code, body, _retry_after(response) if response.status_code == 429 else None)
    finally:
        logger.log(
            logging.INFO if outcome in ("200", "201") else logging.WARNING,
            "provider call op=%s status=%s requestId=%s providerRequestId=%s",
            operation.id, outcome, request_id.get() or "-", provider_request_id,
        )


async def _call(operation, check, deadline, method, path, **request):
    reply = await _exchange(operation, check, deadline, method, path, **request)
    if reply.status != 200:
        raise _failure(reply.status, reply.body, reply.retry_after)
    return reply.body


async def search(request):
    """One leg, never retried (A35). `request` is a contract SearchRequest."""
    deadline = asyncio.get_running_loop().time() + config.PROVIDER_TIMEOUT_SECONDS
    return await _call(SEARCH, _check_search, deadline, "POST", "/offers/search", json=request)


def _wait(error):
    """Seconds to wait before the second getOffer attempt, or None when it must not be retried."""
    if error.code == "RATE_LIMITED":
        return error.retry_after or DEFAULT_RATE_LIMIT_WAIT
    if error.code == "PROVIDER_UNAVAILABLE":
        return 2 + random.random()
    return None  # a timeout, 401 or invalid answer: not proof that waiting helps


async def get_offer(offer_id):
    """The provider's current quote. At most two attempts, and only after a 429 or 503 (A40).

    One deadline covers both attempts and the wait between them (O7). A wait that does not fit in the time left is not
    slept: the first error is answered at once. A deadline that expires during the second attempt is PROVIDER_UNREACHABLE."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.PROVIDER_TIMEOUT_SECONDS
    # Unencoded, "offer-hotel-001/../offer-hotel-002" reached another offer.
    path = f"/offers/{quote(offer_id, safe='')}"
    for attempt in (1, 2):
        try:
            return await _call(GET_OFFER, _check_offer, deadline, "GET", path)
        except ProviderError as error:
            wait = _wait(error)
            if attempt == 2 or wait is None or wait > MAX_WAIT or loop.time() + wait >= deadline:
                raise
            await asyncio.sleep(wait)


async def create_booking(key, request):
    """One POST, never retried here (A14): only the user resends it. `request` is the stored CreateBookingRequest, and
    it is sent as the same bytes every time (sorted keys), whatever order the database returned its keys in."""
    deadline = asyncio.get_running_loop().time() + config.PROVIDER_TIMEOUT_SECONDS
    content = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "Idempotency-Key": key}
    return await _exchange(
        CREATE_BOOKING, _check_booking(request["clientReference"]), deadline, "POST", "/bookings", content=content, headers=headers
    )


async def get_booking_by_reference(reference):
    """A read (A20). Never retried here: the reconciliation loop is the retry."""
    deadline = asyncio.get_running_loop().time() + config.PROVIDER_TIMEOUT_SECONDS
    path = f"/bookings/by-reference/{quote(reference, safe='')}"
    return await _exchange(GET_BOOKING_BY_REFERENCE, _check_booking(reference), deadline, "GET", path)
