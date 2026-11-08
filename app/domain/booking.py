"""Pure booking rules: what a provider answer does to an attempt, which moves are allowed, and how an attempt is shown.

Nothing here touches the database or the network. A-numbers and section numbers refer to docs/CONTRACT_NOTES.md (2.8 is the
outcome table, 2.7 rule 7 the monotonic rule).
"""
from datetime import UTC
from typing import NamedTuple

OPEN = ("unknown", "pending")  # the outcome can still change
CLOSED_WITHOUT_BOOKING = ("rejected", "failed")  # another attempt may start, and the item may be deleted

# New state -> the states it may replace. unknown moves anywhere. pending moves only forward. confirmed, failed and
# rejected never change (2.7 rule 7). None is "no state change": a read that learned nothing.
MAY_REPLACE = {
    None: OPEN,
    "unknown": ("unknown",),
    "pending": OPEN,
    "confirmed": OPEN,
    "failed": OPEN,
    "rejected": ("unknown",),
}


class Outcome(NamedTuple):
    state: str
    booking: dict | None = None  # the provider's Booking, raw (A26)
    error: dict | None = None  # {code, message[, retryAfterSeconds]}: the latest refusal or failure


def adopt(booking):
    """The state comes from `status`, never from the HTTP code (2.2). A valid Booking clears the last error."""
    return Outcome(booking["status"], booking)


def _error(code, message, **extra):
    return {"code": code, "message": message, **extra}


def from_post(first, status, body, retry_after):
    """The 2.8 table for a declared status with a valid body. None means look the booking up by reference (the two 409
    conflicts), which needs the network, so the service does it. `first` is the attempt's first POST, False for a retry."""
    refusal = _error(body["code"], body["message"]) if status >= 400 else None
    match status:
        case 200 | 201:
            return adopt(body)
        case 404:
            return Outcome("rejected", None, refusal)
        case 409 if body["code"] in ("OFFER_CHANGED", "OFFER_UNAVAILABLE"):
            return Outcome("rejected", None, refusal)
        case 409:  # IDEMPOTENCY_CONFLICT, REFERENCE_CONFLICT
            return None
        case 400:  # our bug: the first POST has nothing to resend, a retry may still have booked something earlier
            return Outcome("rejected" if first else "unknown", None, refusal)
        case 401:  # an operator problem, never shown as the provider's own text (A38)
            return Outcome("unknown", None, _error(body["code"], "The provider integration is misconfigured. Try again later."))
        case 429:
            extra = {"retryAfterSeconds": retry_after} if retry_after is not None else {}
            return Outcome("unknown", None, {**refusal, **extra})
        case _:  # 503
            return Outcome("unknown", None, refusal)


def same_terms(booking, request):
    """REFERENCE_CONFLICT adopts the booking found only if it is for the terms this attempt sent (3.2)."""
    return booking["offer"]["id"] == request["offerId"] and booking["offer"]["revision"] == request["expectedOfferRevision"]


def _next_step(attempt):
    state, error = attempt.state, attempt.last_error or {}
    match state:
        case "unknown":
            return (
                "The outcome of this booking is not known yet. The application keeps checking with the provider. "
                "You can retry with the same terms."
            )
        case "pending":
            return "The provider is processing this booking. This page updates when it has an answer."
        case "confirmed":
            return "This item is booked."
        case "failed":
            # A16: `failure` is only prose-guaranteed, so its absence is not an error.
            why = attempt.booking.get("failure", {}).get("message", "The provider could not complete this booking.")
            return f"{why} To try again, review the current terms and start a new booking."
        case _:
            if error.get("code") == "OFFER_CHANGED":
                return "No booking was made: the offer changed. Refresh the item to see the new terms."
            if error.get("code") in ("OFFER_UNAVAILABLE", "NOT_FOUND"):
                return "No booking was made: the offer is no longer available. Search again."
            return f"No booking was made: {error.get('message', 'the provider refused it')}"


def attempt_view(attempt):
    """`attempt` is a BookingAttempt row. Optional fields are omitted, never null."""
    view = {
        "id": str(attempt.id),
        "itemId": str(attempt.item_id),
        "attempt": attempt.attempt_no,
        "state": attempt.state,
        "clientReference": attempt.client_reference,
        "requested": {
            "offerId": attempt.request["offerId"],
            "expectedOfferRevision": attempt.request["expectedOfferRevision"],
            "expectedPriceUsd": attempt.request["expectedPrice"]["amount"],
        },
    }
    if attempt.booking is not None:
        view["booking"] = attempt.booking
    if attempt.last_error is not None:
        view["lastError"] = attempt.last_error
    view["requestedBy"] = attempt.requested_by
    view["createdAt"] = attempt.created_at.astimezone(UTC).isoformat()
    if attempt.last_checked_at is not None:
        view["lastCheckedAt"] = attempt.last_checked_at.astimezone(UTC).isoformat()
    view["nextStep"] = _next_step(attempt)
    return view
