"""Pure itinerary rules: how a stored offer is shown, which issues the itinerary has, and what it costs.

Nothing here touches the database or the network, and nothing is stored: issues and budget are computed at read time.
`trip` is the trip view (app/services/trips.py), `items` are {id, position, offer[, booking]} ordered by position, `offer`
is the provider's snapshot and `booking` is the latest attempt's view (app/domain/booking.py), absent when there is none. A-numbers refer to docs/CONTRACT_NOTES.md.
"""
from decimal import Decimal

from app.domain.booking import OPEN
from app.domain.rules import parse_date, parse_instant
from app.money import format_amount, parse_amount

ROUNDING_RULE = "Amounts are exact sums of the provider's two-decimal prices, so nothing is rounded when they are added. Any amount that needs a third decimal rounds half up."

RULES = [
    "Every price is an estimate until a booking confirms it. A confirmed item counts at the price in the provider's booking, which later quote changes never touch, and every other item stays an estimate.",
    "An offer's price is the total for its whole party and its whole stay or segment. It is never multiplied by travelers or nights.",
    "Every item counts toward the budget, available or not, expired or not. The difference is the absolute gap between the total and the budget, and the trip is over budget only when the total is above it.",
    "A quote is expired when the application's clock has reached its validUntil. That is a hint: the provider decides when a booking is attempted. An item is bookable when it is available and not expired, and no booking of it is open or confirmed. A confirmed item raises no price-expired or unavailable issue, because its price is the one the provider accepted.",
    "A destination needs an accommodation item whose destination matches it, ignoring case. A transport or activity does not count as a stay.",
    "A transport offer with no origin is missing information, not an error.",
    "The party is checked by comparing the offer's travelers with the trip's travelers.",
    "Dates: an item is outside the trip when the calendar date written in its startsAt is before the trip's start, or the one in its endsAt is after the trip's end. The date as written is used, not the date in UTC.",
    "Overlaps compare real instants, so 10:00+01:00 and 09:00Z are the same moment. Two items overlap when each starts before the other ends. Touching is not overlapping.",
    "Only some pairs conflict: two accommodations, two transports, two activities, or a transport and an activity. An accommodation never conflicts with a transport or an activity.",
]


def price_kind(item):
    return "confirmed" if item.get("booking", {}).get("state") == "confirmed" else "estimate"


def quote(offer, now, kind="estimate"):
    """An offer as shown to the user. `expired` uses the application's clock (A28)."""
    expired = now >= parse_instant(offer["validUntil"])
    return {"offer": offer, "priceKind": kind, "expired": expired, "bookable": offer["available"] and not expired}


def item_view(item, now):
    shown = {"id": item["id"], "position": item["position"], **quote(item["offer"], now, price_kind(item))}
    if item.get("booking", {}).get("state") in (*OPEN, "confirmed"):
        shown["bookable"] = False  # an attempt is open or done: booking again is refused anyway (A12)
    if "booking" in item:
        shown["booking"] = item["booking"]
    return shown


def _issue(code, message, item_ids, **extra):
    return {"code": code, "message": message, "itemIds": item_ids, **extra}


def _conflicting(first, second):
    return first == second or {first, second} == {"transport", "activity"}


def issues(trip, items, now):
    """In a fixed order: by rule, then by position, so two reads of the same itinerary are identical."""
    found = []
    starts_on, ends_on = parse_date(trip["startsOn"]), parse_date(trip["endsOn"])
    for item in items:
        offer = item["offer"]
        if offer["category"] == "transport" and "origin" not in offer:  # A30
            found.append(_issue("MISSING_ORIGIN", f"{offer['title']} has no origin.", [item["id"]]))
    stays = {item["offer"]["destination"].casefold() for item in items if item["offer"]["category"] == "accommodation"}
    seen = set()
    for destination in trip["destinations"]:
        key = destination.casefold()
        if key not in stays and key not in seen:
            found.append(_issue("DESTINATION_WITHOUT_STAY", f"No accommodation is planned in {destination}.", [], destination=destination))
        seen.add(key)
    for item in items:
        offer = item["offer"]
        if offer["travelers"] != trip["travelers"]:  # A22
            message = f"{offer['title']} is for {offer['travelers']} travelers, and the trip has {trip['travelers']}."
            found.append(_issue("PARTY_MISMATCH", message, [item["id"]]))
    for item in items:
        if price_kind(item) == "estimate" and now >= parse_instant(item["offer"]["validUntil"]):  # a confirmed price is the accepted one
            found.append(_issue("PRICE_EXPIRED", f"The quote for {item['offer']['title']} has expired.", [item["id"]]))
    for item in items:
        if price_kind(item) == "estimate" and not item["offer"]["available"]:
            found.append(_issue("UNAVAILABLE", f"{item['offer']['title']} is no longer available.", [item["id"]]))
    for item in items:
        offer = item["offer"]
        # A31: the literal local date. An aware datetime keeps the fields as written.
        if parse_instant(offer["startsAt"]).date() < starts_on or parse_instant(offer["endsAt"]).date() > ends_on:
            found.append(_issue("OUTSIDE_TRIP_DATES", f"{offer['title']} falls outside the trip dates.", [item["id"]]))
    spans = [(parse_instant(item["offer"]["startsAt"]), parse_instant(item["offer"]["endsAt"])) for item in items]
    # ponytail: every pair is compared, O(n^2). Fine for tens of items. Sort by start and sweep if an itinerary grows large.
    for i, first in enumerate(items):
        for j in range(i + 1, len(items)):
            second = items[j]
            categories = first["offer"]["category"], second["offer"]["category"]
            if _conflicting(*categories) and spans[i][0] < spans[j][1] and spans[j][0] < spans[i][1]:
                message = f"{first['offer']['title']} overlaps {second['offer']['title']}."
                found.append(_issue("SCHEDULE_OVERLAP", message, [first["id"], second["id"]]))
    return found


def budget(trip, items):
    lines, totals = [], {"estimate": Decimal("0.00"), "confirmed": Decimal("0.00")}
    for item in items:
        offer, kind = item["offer"], price_kind(item)
        # A confirmed item costs what the provider accepted (US-05, A26), not what the snapshot says now.
        priced = item["booking"]["booking"]["offer"] if kind == "confirmed" else offer
        amount = parse_amount(priced["price"]["amount"])  # Decimal("0.00") is falsy: a free activity keeps its line
        totals[kind] += amount
        lines.append(
            {"itemId": item["id"], "title": offer["title"], "category": offer["category"], "amountUsd": format_amount(amount), "priceKind": kind}
        )
    total, limit = totals["estimate"] + totals["confirmed"], parse_amount(trip["budgetUsd"])
    return {
        "currency": "USD",
        "lines": lines,
        "estimatedUsd": format_amount(totals["estimate"]),
        "confirmedUsd": format_amount(totals["confirmed"]),
        "totalUsd": format_amount(total),
        "budgetUsd": format_amount(limit),
        "differenceUsd": format_amount(abs(total - limit)),
        "overBudget": total > limit,
        "roundingRule": ROUNDING_RULE,
    }


def view(trip, items, now):
    return {
        "tripId": trip["id"],
        "version": trip["version"],
        "items": [item_view(item, now) for item in items],
        "issues": issues(trip, items, now),
        "rules": RULES,
        "budget": budget(trip, items),
    }
