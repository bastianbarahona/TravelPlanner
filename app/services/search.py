"""Search: one provider request per destination (A37), run concurrently, never merged."""
import asyncio
import logging
from datetime import UTC, datetime

from fastapi.concurrency import run_in_threadpool

from app.domain.errors import ValidationFailed
from app.domain.itinerary import quote
from app.domain.rules import date_errors, parse_date
from app.providers import client
from app.services.trips import get_trip

logger = logging.getLogger("uvicorn.error")
MAX_DESTINATIONS = 20


def _plan(session, access, body):
    """The provider requests, or a 422. Runs before any provider call, and leaves no connection held."""
    trip = get_trip(session, access.user, access.trip_id)
    session.rollback()
    destinations = body.destinations if body.destinations is not None else trip["destinations"]
    starts_on = parse_date(body.starts_on if body.starts_on is not None else trip["startsOn"])
    ends_on = parse_date(body.ends_on if body.ends_on is not None else trip["endsOn"])
    errors = date_errors(starts_on, ends_on, check_start=True)  # A36
    if not 1 <= len(destinations) <= MAX_DESTINATIONS:
        errors.append({"field": "destinations", "message": f"Search 1 to {MAX_DESTINATIONS} destinations."})
    if body.category == "transport" and body.origin is None:
        errors.append({"field": "origin", "message": "Transport needs an origin."})
    if errors:
        raise ValidationFailed(errors)
    requests = []
    for destination in destinations:
        request = {
            "category": body.category,
            "destination": destination,
            "startsOn": starts_on.isoformat(),
            "endsOn": ends_on.isoformat(),
            "travelers": trip["travelers"],
        }
        if body.category == "transport":  # A33: the provider ignores it elsewhere, so it is not sent
            request["origin"] = body.origin
        if body.provider_id is not None:  # absent, never null (CONTRACT_NOTES 1.0, rule 1)
            request["providerId"] = body.provider_id
        requests.append(request)
    return requests


def _leg(destination, result, now):
    if isinstance(result, BaseException):
        if not isinstance(result, client.ProviderError):
            logger.error("search leg failed unexpectedly", exc_info=result)
            result = client.ProviderError(500, "INTERNAL_ERROR", "This search failed unexpectedly. Try again.")
        error = {"code": result.code, "message": result.message}
        if result.retry_after is not None:
            error["retryAfterSeconds"] = result.retry_after
        return {"destination": destination, "status": "failed", "options": [], "warnings": [], "error": error}
    offers = result["offers"]
    status = "partial" if result["partial"] else "complete" if offers else "empty"  # A35: partial with no offers is not "empty"
    return {
        "destination": destination,
        "status": status,
        "options": [quote(offer, now) for offer in offers],
        "warnings": result["warnings"],
        "searchedAt": result["searchedAt"],
    }


async def search_trip(session, access, body):
    requests = await run_in_threadpool(_plan, session, access, body)
    results = await asyncio.gather(*(client.search(request) for request in requests), return_exceptions=True)
    now = datetime.now(UTC)
    return {"legs": [_leg(request["destination"], result, now) for request, result in zip(requests, results)]}
