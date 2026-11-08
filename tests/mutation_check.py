"""Proves the suites can fail. Not collected by pytest. Run: python tests/mutation_check.py

Each mutation breaks one rule in a temporary source copy, then expects pytest to fail.
A survivor is a rule no test protects. A stale entry means the source changed and the
mutation no longer applies. A false kill means pytest errored instead of detecting the
changed behavior. Mock numbers come from provider-mock/README.md; app mutations are
A01 onward. Pass ``app`` to run only app mutations.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = "main.py"
FIXTURES = "fixtures.json"

REFERENCE_CHECK = """        if find_booking(db, caller, "client_reference", request_body["clientReference"]):
            raise ApiError(
                409, "REFERENCE_CONFLICT", "This client reference belongs to an existing booking request."
            )
"""
AVAILABILITY_CHECK = """        if not offer["available"]:
            raise ApiError(409, "OFFER_UNAVAILABLE", "The offer is no longer available.")
"""
ACCEPT = "        return 201, accept_booking("

MOCK_MUTATIONS = [
    # Test 1: every response validates against the contract.
    ("1", "timestamps lose their offset", MAIN, [('moment.isoformat().replace("+00:00", "Z")', "moment.replace(tzinfo=None).isoformat()")]),
    ("1", "search response gains a field", MAIN, [('"searchedAt": to_timestamp(now()),', '"searchedAt": to_timestamp(now()), "extra": None,')]),
    ("1", "429 loses Retry-After", MAIN, [('{"Retry-After": RETRY_AFTER_SECONDS}', "None")]),
    ("1", "fixture amount with one decimal", FIXTURES, [('"amount": "45.00"', '"amount": "45.0"')]),
    ("1", "error body loses requestId", MAIN, [('{"code": code, "message": message, "requestId": request_id}', '{"code": code, "message": message}')]),
    # Test 2: prose-only rules.
    ("2", "immediate failure without failure", MAIN, [('    if booking["status"] == "failed":\n        booking["failure"] = BOOKING_FAILURE\n    is_pending', "    is_pending")]),
    ("2", "resolved failure without failure", MAIN, [('        if booking["status"] == "failed":\n            booking["failure"] = BOOKING_FAILURE\n        db.execute', "        db.execute")]),
    ("2", "failure on every booking", MAIN, [('    if booking["status"] == "failed":\n        booking["failure"] = BOOKING_FAILURE\n    is_pending', '    booking["failure"] = BOOKING_FAILURE\n    is_pending')]),
    ("2", "endsOn before startsOn accepted", MAIN, [('if query["endsOn"] < query["startsOn"]:', "if False:")]),
    ("2", "fixture endsAt before startsAt", FIXTURES, [('"endsAt": "2030-06-10T11:45:00+01:00"', '"endsAt": "2030-06-10T08:45:00+01:00"')]),
    ("2", "updatedAt before createdAt", MAIN, [('booking["updatedAt"] = row["resolves_at"]', 'booking["updatedAt"] = "2000-01-01T00:00:00Z"')]),
    ("2", "partial false with warnings", MAIN, [('"partial": bool(failed),', '"partial": False,')]),
    ("2", "transport fixture without origin", FIXTURES, [('      "origin": "London",\n      "startsAt": "2030-06-10T09:00:00+01:00",', '      "startsAt": "2030-06-10T09:00:00+01:00",')]),
    # Test 3: replay.
    ("3", "replay answers 201", MAIN, [("return 200, settle(db, existing, moment)", "return 201, settle(db, existing, moment)")]),
    ("3", "replay returns the stored state", MAIN, [("return 200, settle(db, existing, moment)", 'return 200, json.loads(existing["doc"])')]),
    ("3", "replay refused after the quote expires", MAIN, [("        if existing:\n", '        if existing and moment < datetime.fromisoformat(json.loads(existing["doc"])["offer"]["validUntil"]):\n')]),
    # Test 4: concurrency.
    ("4", "plain BEGIN instead of BEGIN IMMEDIATE", MAIN, [('db.execute("BEGIN IMMEDIATE")', 'db.execute("BEGIN")')]),
    # Test 5: the 409 cases and their order.
    ("5", "no IDEMPOTENCY_CONFLICT", MAIN, [('if existing["request"] != canonical:', "if False:")]),
    ("5", "no REFERENCE_CONFLICT", MAIN, [(REFERENCE_CHECK, "")]),
    ("5", "reference checked after the offer", MAIN, [(REFERENCE_CHECK, ""), (ACCEPT, REFERENCE_CHECK + ACCEPT)]),
    ("5", "availability checked after the terms", MAIN, [(AVAILABILITY_CHECK, ""), (ACCEPT, AVAILABILITY_CHECK + ACCEPT)]),
    ("5", "no OFFER_UNAVAILABLE", MAIN, [(AVAILABILITY_CHECK, "")]),
    ("5", "revision not compared", MAIN, [('            or request_body["expectedOfferRevision"] != offer["revision"]\n', "")]),
    ("5", "price not compared", MAIN, [('            or request_body["expectedPrice"] != offer["price"]\n', "")]),
    ("5", "body compared in property order", MAIN, [("sort_keys=True", "sort_keys=False")]),
    # Test 6: durability.
    ("6", "database wiped on start", MAIN, [("DB_PATH.parent.mkdir(parents=True, exist_ok=True)", "DB_PATH.parent.mkdir(parents=True, exist_ok=True); DB_PATH.unlink(missing_ok=True)")]),
    ("6", "offers reseeded on start", MAIN, [("INSERT OR IGNORE INTO offers", "INSERT OR REPLACE INTO offers")]),
    # Test 7: caller isolation.
    ("7", "lookups ignore the caller", MAIN, [('f"SELECT * FROM bookings WHERE caller = ? AND {column} = ?", (caller, value)', 'f"SELECT * FROM bookings WHERE {column} = ?", (value,)')]),
    ("7", "references unique globally", MAIN, [("UNIQUE (caller, client_reference)", "UNIQUE (client_reference)")]),
    ("7", "keys unique globally", MAIN, [("UNIQUE (caller, idempotency_key)", "UNIQUE (idempotency_key)")]),
    # Test 8: the expiry boundary.
    ("8", "acceptance allowed at validUntil", MAIN, [('            moment >= datetime.fromisoformat(offer["validUntil"])\n            or', '            moment > datetime.fromisoformat(offer["validUntil"])\n            or')]),
    ("8", "expiry never checked", MAIN, [('            moment >= datetime.fromisoformat(offer["validUntil"])\n            or', "            False\n            or")]),
    ("8", "quote refreshed only after validUntil", MAIN, [('    if moment >= datetime.fromisoformat(offer["validUntil"]):\n        offer = {', '    if moment > datetime.fromisoformat(offer["validUntil"]):\n        offer = {')]),
    # Test 9: searches.
    ("9", "total failure answers 200", MAIN, [("if queried and len(failed) == len(queried):", "if False:")]),
    ("9", "single rate limit answers 503", MAIN, [('if len(queried) == 1 and "RATE_LIMITED" in failed.values():', "if False:")]),
    ("9", "any rate limit answers 429", MAIN, [('if len(queried) == 1 and "RATE_LIMITED" in failed.values():', "if True:")]),
    ("9", "suppliers queried one by one", MAIN, [('outcomes = await asyncio.gather(*(query_supplier(supplier, query["destination"]) for supplier in queried))', 'outcomes = [await query_supplier(supplier, query["destination"]) for supplier in queried]')]),
    ("9", "failed supplier still contributes offers", MAIN, [("set(queried) - set(failed)", "set(queried)")]),
    ("9", "timeout does not wait for the deadline", MAIN, [("await asyncio.sleep(SUPPLIER_DEADLINE)", "pass")]),
    # Test 10: invalid input.
    ("10", "framework errors keep their default body", MAIN, [("@app.exception_handler(HTTPException)\n", "")]),
    ("10", "bodies not validated", MAIN, [("error = next(validator.iter_errors(body), None)", "error = None")]),
    ("10", "date format not checked", MAIN, [("Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)", "Draft202012Validator(schema)")]),
    ("10", "key length not checked", MAIN, [("if not 1 <= len(key) <= 128:", "if not key:")]),
    ("10", "missing key accepted", MAIN, [("if not 1 <= len(key) <= 128:", "if len(key) > 128:")]),
    ("10", "deep nesting answers 503", MAIN, [("except (ValueError, RecursionError):", "except ValueError:")]),
    ("10", "trailing slash redirects", MAIN, [("    redirect_slashes=False,\n", "")]),
    ("10", "generated openapi.json exposed", MAIN, [("    openapi_url=None,\n", "")]),
    # Rules beyond the ten.
    ("-", "destination is case-sensitive", MAIN, [('offer["destination"].casefold() == query["destination"].casefold()', 'offer["destination"] == query["destination"]')]),
    ("-", "origin not compared", MAIN, [('        and (not is_transport or offer["origin"].casefold() == query["origin"].casefold())\n', "")]),
    ("-", "party size not compared", MAIN, [('        and offer["travelers"] == query["travelers"]\n', "")]),
    ("-", "window start exclusive", MAIN, [('query["startsOn"] <= offer["startsAt"][:10]', 'query["startsOn"] < offer["startsAt"][:10]')]),
    ("-", "window end exclusive", MAIN, [('offer["endsAt"][:10] <= query["endsOn"]', 'offer["endsAt"][:10] < query["endsOn"]')]),
    ("-", "window compares UTC dates", MAIN, [('query["startsOn"] <= offer["startsAt"][:10]', 'query["startsOn"] <= datetime.fromisoformat(offer["startsAt"]).astimezone(UTC).date().isoformat()')]),
    ("-", "unavailable offers hidden from search", MAIN, [('if offer["providerId"] in suppliers and matches(offer, query)', 'if offer["available"] and offer["providerId"] in suppliers and matches(offer, query)')]),
    ("-", "unknown supplier ignored", MAIN, [("if provider_id not in suppliers:", "if False:"), ('return [provider_id] if query["category"] in suppliers[provider_id] else []', "return []")]),
    ("-", "providerId ignored", MAIN, [('    if provider_id is None:\n', "    if True:\n")]),
    ("-", "every read mints a revision", MAIN, [('    if moment >= datetime.fromisoformat(offer["validUntil"]):\n        offer = {', "    if True:\n        offer = {")]),
    ("-", "quotes never refreshed", MAIN, [('    if moment >= datetime.fromisoformat(offer["validUntil"]):\n        offer = {', "    if False:\n        offer = {")]),
    ("-", "offer change keeps the revision", MAIN, [(', "revision": new_quote(now())["revision"]}', "}")]),
    ("-", "pending resolves immediately", MAIN, [('booking["status"] == "pending" and moment >= datetime.fromisoformat(row["resolves_at"])', 'booking["status"] == "pending"')]),
    ("-", "pending never resolves", MAIN, [('booking["status"] == "pending" and moment >= datetime.fromisoformat(row["resolves_at"])', "False")]),
    ("-", "pending resolves after the delay, not at it", MAIN, [('moment >= datetime.fromisoformat(row["resolves_at"])', 'moment > datetime.fromisoformat(row["resolves_at"])')]),
    ("-", "resolved state not saved", MAIN, [('        db.execute("UPDATE bookings SET doc = ? WHERE id = ?", (json.dumps(booking), booking["id"]))\n', "")]),
    ("-", "every booking confirmed", MAIN, [('FIXTURES["bookingOutcomes"].get(offer["id"], {"initial": "confirmed"})', '{"initial": "confirmed"}')]),
    ("-", "snapshot is not the accepted offer", MAIN, [('"offer": offer,', '"offer": {**offer, "title": "Changed"},')]),
    ("-", "encoded slash breaks reference lookup", MAIN, [("{client_reference:path}", "{client_reference}")]),
    ("-", "fault checked before validation", MAIN, [("    authenticate(request)\n    query = parse_body", "    authenticate(request)\n    check_fault()\n    query = parse_body")]),
    ("-", "validation before authentication", MAIN, [("    authenticate(request)\n    query = parse_body(await request.body(), SEARCH_REQUEST)\n", "    query = parse_body(await request.body(), SEARCH_REQUEST)\n    authenticate(request)\n")]),
    ("-", "booking ignores faults", MAIN, [("CREATE_BOOKING_REQUEST)\n    check_fault()\n", "CREATE_BOOKING_REQUEST)\n")]),
    ("-", "offer read ignores faults", MAIN, [("    authenticate(request)\n    check_fault()\n    with transaction() as db:\n        row = db.execute(\"SELECT doc FROM offers", "    authenticate(request)\n    with transaction() as db:\n        row = db.execute(\"SELECT doc FROM offers")]),
    ("-", "any bearer token accepted", MAIN, [("    if caller is None:\n", "    if False:\n")]),
    ("-", "control always on", MAIN, [("if CONTROL_ENABLED:", "if True:")]),
]


DESTINATIONS_AFTER_GUARD = """    # Optimistic lock. A concurrent writer that got here first leaves zero rows to update.
    updated = session.execute(
        update(Trip).where(Trip.id == trip_id, Trip.version == version).values(**values, version=Trip.version + 1)
    )
    if updated.rowcount == 0:
        raise version_conflict(session, caller, trip_id)
    if "destinations" in changes:  # only after the guarded update won, in the same transaction
        session.execute(delete(TripDestination).where(TripDestination.trip_id == trip_id))
        session.execute(
            insert(TripDestination),
            [{"trip_id": trip_id, "position": i, "name": name} for i, name in enumerate(changes["destinations"])],
        )
"""
DESTINATIONS_BEFORE_GUARD = """    if "destinations" in changes:
        session.execute(delete(TripDestination).where(TripDestination.trip_id == trip_id))
        session.execute(
            insert(TripDestination),
            [{"trip_id": trip_id, "position": i, "name": name} for i, name in enumerate(changes["destinations"])],
        )
    # Optimistic lock. A concurrent writer that got here first leaves zero rows to update.
    updated = session.execute(
        update(Trip).where(Trip.id == trip_id, Trip.version == version).values(**values, version=Trip.version + 1)
    )
    if updated.rowcount == 0:
        raise version_conflict(session, caller, trip_id)
"""
BAD_JSON_BRANCH = """    if status == 400:
        # FastAPI's 400 for a body it cannot read: undecodable JSON (invalid UTF-8, Python's int digit limit) answers like
        # malformed JSON; a client that disconnected mid-body gets none. The digit limit stays on: denial-of-service guard.
        return error_response(422, "VALIDATION_FAILED", "The request is invalid.", {"errors": [{"field": "body", "message": BAD_JSON}]})
"""

APP_MUTATIONS = [
    # Permissions and route coverage.
    ("A01", "nonparticipant gets through trip_member", "api/deps.py", [('    if role is None:\n        raise not_found()\n', '    if False:\n        raise not_found()\n')]),
    ("A02", "trip organizer stops checking the role", "api/deps.py", [('    if access.role != "organizer":\n', '    if False:\n')]),
    ("A03", "current user accepts any authorization scheme", "api/deps.py", [('TOKENS.get(token) if scheme.lower() == "bearer" else None', 'TOKENS.get(token)')]),
    ("A04", "collection routes lose authentication dependencies", "api/trips.py", [
        ('collection = APIRouter(prefix="/trips", dependencies=[Depends(current_user)])', 'collection = APIRouter(prefix="/trips")'),
        ('def create_trip(body: CreateTrip, user: str = Depends(current_user), session=Depends(get_session)):', 'def create_trip(body: CreateTrip, user: str = "ana", session=Depends(get_session)):'),
        ('def list_trips(user: str = Depends(current_user), session=Depends(get_session)):', 'def list_trips(user: str = "ana", session=Depends(get_session)):'),
    ]),
    ("A05", "a new organizer route gets no dependencies", "api/trips.py", [
        ('organizers = APIRouter(prefix="/trips/{tripId}", dependencies=[Depends(trip_organizer)])', 'organizers = APIRouter(prefix="/trips/{tripId}")'),
        ('\n\n@collection.post("", status_code=201)', '\n\n@organizers.post("/extra")\ndef extra():\n    return {}\n\n\n@collection.post("", status_code=201)'),
    ]),
    ("A06", "organizer routes fall back to trip_member", "api/trips.py", [("trip_organizer", "trip_member")]),
    ("A07", "participant routes fall back to trip_member", "api/participants.py", [("trip_organizer", "trip_member")]),
    ("A08", "trip list stops filtering by caller", "services/trips.py", [('    return _snapshot_views(session, caller, Trip.id.in_(mine))', '    return _snapshot_views(session, caller)')]),
    ("A09", "canonical UUID check is removed", "api/deps.py", [('    if str(trip_id) != tripId.lower():  # UUID() also takes braces, urn: and underscores\n        raise not_found()\n', '')]),
    ("A10", "organizer rows can be changed or removed", "services/participants.py", [('    if role == "organizer":\n', '    if False:\n')]),
    ("A11", "known_user accepts an unknown identity", "api/deps.py", [('    if userId not in IDENTITIES:\n', '    if False:\n')]),
    ("A12", "health route disappears from the schema", "main.py", [('@app.get("/health")', '@app.get("/health", include_in_schema=False)')]),
    # Money boundaries and representation.
    ("A13", "amount loses its integer digit bound", "money.py", [('(0|[1-9][0-9]{0,9})', '(0|[1-9][0-9]*)')]),
    ("A14", "amount accepts one decimal", "money.py", [('[0-9]{2}")', '[0-9]{1,2}")')]),
    ("A15", "amount accepts a leading zero", "money.py", [('r"(0|[1-9][0-9]{0,9})\\.[0-9]{2}"', 'r"[0-9]{1,10}\\.[0-9]{2}"')]),
    ("A16", "amount validation uses match instead of fullmatch", "money.py", [('AMOUNT.fullmatch(text)', 'AMOUNT.match(text)')]),
    ("A17", "formatted amount loses its two decimals", "money.py", [
        ('    return format(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")', '    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)).rstrip("0").rstrip(".")'),
    ]),
    ("A18", "a truth test skips a zero budget patch", "services/trips.py", [('    if "budget_usd" in changes:\n', '    if parse_amount(changes.get("budget_usd", "0.00")):\n')]),
    # Optimistic versioning and participant concurrency.
    ("A19", "version leaves the guarded UPDATE", "services/trips.py", [('where(Trip.id == trip_id, Trip.version == version)', 'where(Trip.id == trip_id)')]),
    ("A20", "trip version increment is removed", "services/trips.py", [('version=Trip.version + 1', 'version=version')]),
    ("A21", "zero rowcount is ignored", "services/trips.py", [('    if updated.rowcount == 0:\n        raise version_conflict(session, caller, trip_id)\n', '')]),
    ("A22", "destinations are replaced before the version guard", "services/trips.py", [(DESTINATIONS_AFTER_GUARD, DESTINATIONS_BEFORE_GUARD)]),
    ("A23", "version conflict current state is empty", "services/trips.py", [('{"current": read_trip(session, caller, trip_id)}', '{"current": {}}')]),
    ("A24", "participant insert loses ON CONFLICT", "services/participants.py", [('        .on_conflict_do_nothing()\n', '')]),
    ("A25", "participant insert becomes check then insert", "services/participants.py", [
        ('    row = session.execute(\n        insert(TripParticipant)', '    if session.scalar(select(TripParticipant.user_id).where(TripParticipant.trip_id == trip_id, TripParticipant.user_id == user_id)) is not None:\n        raise ApiError(409, "PARTICIPANT_EXISTS", "That user is already on the trip.")\n    row = session.execute(\n        insert(TripParticipant)'),
        ('        .on_conflict_do_nothing()\n', ''),
    ]),
    # UTC date rules and error normalization.
    ("A26", "UTC date becomes date.today", "domain/rules.py", [('datetime.now(UTC).date()', 'date.today()')]),
    ("A27", "one day start tolerance is removed", "domain/rules.py", [(' - timedelta(days=1)', '')]),
    ("A28", "equal trip dates are refused", "domain/rules.py", [('    if ends_on < starts_on:\n', '    if ends_on <= starts_on:\n')]),
    ("A29", "patch start-date check is ignored", "services/trips.py", [('check_start=starts_on.isoformat() != stored["startsOn"]', 'check_start=False')]),
    ("A30", "undecodable JSON keeps FastAPI's 400", "main.py", [(BAD_JSON_BRANCH, '')]),
    ("A31", "unknown route gets a different message", "main.py", [('404: not_found().message', '404: "Route not found."')]),
    ("A32", "organizer inserted after the trip is committed", "services/trips.py", [
        ('    session.execute(insert(TripParticipant).values(trip_id=trip_id, user_id=caller, role="organizer"))', '    session.commit()\n    session.execute(insert(TripParticipant).values(trip_id=trip_id, user_id=caller, role="organizer"))'),
    ]),
    ("A33", "version is no longer a strict integer", "api/schemas.py", [
        ('    version: int = Field(strict=True)', '    version: int'),
    ]),
    ("A34", "request bodies accept unknown fields", "api/schemas.py", [
        ('    model_config = ConfigDict(extra="forbid", alias_generator=to_camel)', '    model_config = ConfigDict(extra="ignore", alias_generator=to_camel)'),
    ]),
    ("A35", "a validation message echoes the input", "main.py", [
        ('    message = error["msg"].removeprefix("Value error, ")', '    message = error["msg"].removeprefix("Value error, ") + repr(error.get("input"))'),
    ]),
    # Provider contract validation and transport guarantees.
    ("A36", "provider schemas are not validated", "providers/client.py", [
        ("            validator.validate(body)\n", ""),
    ]),
    ("A37", "provider strings are not cleaned", "providers/client.py", [
        ("            _clean(body)\n", ""),
    ]),
    ("A38", "offer amounts bypass parse_amount", "providers/client.py", [
        ('    parse_amount(offer["price"]["amount"])  # the schema\'s `$` lets "420.00\\n" through, and it has no upper bound\n', ""),
    ]),
    ("A39", "offer instants bypass parse_instant", "providers/client.py", [
        ("        parse_instant(offer[field])\n", "        str(offer[field])\n"),
    ]),
    ("A40", "an undeclared provider status is accepted", "providers/client.py", [
        ('        if validator is None:\n            raise _invalid(f"undeclared status {response.status_code}", response.content)\n', '        if validator is None:\n            validator = Draft202012Validator({"type": "object"})\n'),
    ]),
    ("A41", "the whole-call asyncio timeout is removed", "providers/client.py", [
        ("            async with asyncio.timeout_at(deadline):\n                response = await http.request(method, path, **request)\n", "            response = await http.request(method, path, **request)\n"),
    ]),
    ("A42", "offer id is not percent-encoded", "providers/client.py", [
        ("path = f\"/offers/{quote(offer_id, safe='')}\"", 'path = f"/offers/{offer_id}"'),
    ]),
    ("A43", "getOffer makes only one attempt", "providers/client.py", [
        ("    for attempt in (1, 2):\n", "    for attempt in (1,):\n"),
    ]),
    ("A44", "getOffer waits as long as sixty seconds", "providers/client.py", [
        ("MAX_WAIT = 5  # a longer wait is reported, not slept (A40)", "MAX_WAIT = 60  # mutation: long waits are slept"),
    ]),
    ("A45", "getOffer does not retry 503", "providers/client.py", [
        ('    if error.code == "PROVIDER_UNAVAILABLE":\n        return 2 + random.random()\n', ""),
    ]),
    ("A46", "provider 401 is forwarded as 401", "providers/client.py", [
        ('            return ProviderError(502, "PROVIDER_AUTH", "The provider integration is misconfigured. Try again later.")', '            return ProviderError(401, "PROVIDER_AUTH", "The provider integration is misconfigured. Try again later.")'),
    ]),
    # Search state, concurrency and exact request bodies.
    ("A47", "partial with no offers is reported as empty", "services/search.py", [
        ('    status = "partial" if result["partial"] else "complete" if offers else "empty"  # A35: partial with no offers is not "empty"', '    status = "complete" if offers else "empty"'),
    ]),
    ("A48", "complete and empty search states are swapped", "services/search.py", [
        ('"complete" if offers else "empty"', '"empty" if offers else "complete"'),
    ]),
    ("A49", "search gather drops return_exceptions", "services/search.py", [
        (", return_exceptions=True", ""),
    ]),
    ("A50", "origin is forwarded on non-transport search", "services/search.py", [
        ('        if body.category == "transport":  # A33: the provider ignores it elsewhere, so it is not sent\n', '        if body.origin is not None:\n'),
    ]),
    ("A51", "providerId is sent as null when absent", "services/search.py", [
        ('        if body.provider_id is not None:  # absent, never null (CONTRACT_NOTES 1.0, rule 1)\n            request["providerId"] = body.provider_id\n', '        request["providerId"] = body.provider_id\n'),
    ]),
    ("A52", "search travelers are hard-coded to two", "services/search.py", [
        ('            "travelers": trip["travelers"],', '            "travelers": 2,'),
    ]),
    ("A54", "the search plan keeps its transaction open", "services/search.py", [
        ("    session.rollback()\n    destinations = body.destinations", "    destinations = body.destinations"),
    ]),
    # Itinerary permissions, optimistic locking and refresh.
    ("A55", "trip_editor lets a viewer through", "api/deps.py", [
        ('    if access.role not in ("organizer", "editor"):\n', '    if access.role not in ("organizer", "editor", "viewer"):\n'),
    ]),
    ("A56", "itinerary guarded update loses its version condition", "services/itinerary.py", [
        ("where(Trip.id == access.trip_id, Trip.version == version)", "where(Trip.id == access.trip_id)"),
    ]),
    ("A57", "add skips the Python version check before the provider call", "services/itinerary.py", [
        ("    await run_in_threadpool(_precheck, session, access, version)\n", ""),
    ]),
    ("A58", "find_item drops its concurrent version recheck", "services/itinerary.py", [
        ("        if str(item_id) == text.lower() and version is not None:\n            _check_version(session, access, version)\n        raise not_found()\n", "        raise not_found()\n"),
    ]),
    ("A59", "reorder drops its concurrent version recheck", "services/itinerary.py", [
        ("        _check_version(session, access, version)  # the items may have changed after the first check: that is a 409\n", ""),
    ]),
    ("A60", "refresh bumps the version when the quote is unchanged", "services/itinerary.py", [
        ('    if fresh != find_item(session, access, version, str(item_id))[2]:\n', '    if True:\n'),
    ]),
    # Itinerary issue and budget rules.
    ("A61", "overlap compares timestamp strings", "domain/itinerary.py", [
        ('    spans = [(parse_instant(item["offer"]["startsAt"]), parse_instant(item["offer"]["endsAt"])) for item in items]', '    spans = [(item["offer"]["startsAt"], item["offer"]["endsAt"]) for item in items]'),
    ]),
    ("A62", "touching itinerary items count as overlapping", "domain/itinerary.py", [
        ("spans[i][0] < spans[j][1] and spans[j][0] < spans[i][1]", "spans[i][0] <= spans[j][1] and spans[j][0] <= spans[i][1]"),
    ]),
    ("A63", "accommodation conflicts with activity", "domain/itinerary.py", [
        ('    return first == second or {first, second} == {"transport", "activity"}', '    return first == second or {first, second} in ({"transport", "activity"}, {"accommodation", "activity"})'),
    ]),
    ("A64", "MISSING_ORIGIN condition is removed", "domain/itinerary.py", [
        ('        if offer["category"] == "transport" and "origin" not in offer:  # A30\n', '        if False:\n'),
    ]),
    ("A65", "DESTINATION_WITHOUT_STAY condition is inverted", "domain/itinerary.py", [
        ("        if key not in stays and key not in seen:\n", "        if key in stays and key not in seen:\n"),
    ]),
    ("A66", "PARTY_MISMATCH condition is inverted", "domain/itinerary.py", [
        ('        if offer["travelers"] != trip["travelers"]:  # A22\n', '        if offer["travelers"] == trip["travelers"]:\n'),
    ]),
    ("A67", "PRICE_EXPIRED condition is inverted", "domain/itinerary.py", [
        (' and now >= parse_instant(item["offer"]["validUntil"]):', ' and now < parse_instant(item["offer"]["validUntil"]):'),
    ]),
    ("A68", "UNAVAILABLE condition is inverted", "domain/itinerary.py", [
        (' and not item["offer"]["available"]:', ' and item["offer"]["available"]:'),
    ]),
    ("A69", "OUTSIDE_TRIP_DATES condition is removed", "domain/itinerary.py", [
        ('        if parse_instant(offer["startsAt"]).date() < starts_on or parse_instant(offer["endsAt"]).date() > ends_on:\n', '        if False:\n'),
    ]),
    ("A70", "SCHEDULE_OVERLAP condition is removed", "domain/itinerary.py", [
        ("            if _conflicting(*categories) and spans[i][0] < spans[j][1] and spans[j][0] < spans[i][1]:\n", "            if False:\n"),
    ]),
    ("A71", "budget difference loses abs", "domain/itinerary.py", [
        ('"differenceUsd": format_amount(abs(total - limit))', '"differenceUsd": format_amount(total - limit)'),
    ]),
    ("A72", "equal budget is over budget", "domain/itinerary.py", [
        ('"overBudget": total > limit', '"overBudget": total >= limit'),
    ]),
    ("A73", "format_amount rounds half even", "money.py", [
        (", rounding=ROUND_HALF_UP", ""),
    ]),
    # Migration and model guarantees.
    ("A74", "migration itinerary positions are not deferrable", "db/migrations/versions/0002_itinerary_and_party.py", [
        (", deferrable=True, initially='DEFERRED'", ""),
    ]),
    # A75 was dropped as equivalent: the live schema always comes from the migration, not the model metadata.
    ("A76", "travelers check constraint lower bound is changed", "db/migrations/versions/0002_itinerary_and_party.py", [
        ("'travelers BETWEEN 1 AND 20'", "'travelers BETWEEN 2 AND 20'"),
    ]),
    ("A77", "travelers backfill server default is removed", "db/migrations/versions/0002_itinerary_and_party.py", [
        (", server_default='1'", ""),
    ]),
    # Owner decisions O7, O5 and O8.
    ("A78", "getOffer bounds each attempt instead of using one deadline", "providers/client.py", [
        ('            return await _call(GET_OFFER, _check_offer, deadline, "GET", path)',
         '            return await _call(GET_OFFER, _check_offer, loop.time() + config.PROVIDER_TIMEOUT_SECONDS, "GET", path)'),
    ]),
    ("A79", "getOffer retries when the wait does not fit", "providers/client.py", [
        (" or loop.time() + wait >= deadline", ""),
    ]),
    ("A80", "ready skips its database query", "main.py", [
        ('        with engine.connect() as connection:\n            connection.execute(text("SELECT 1"))\n', '        pass\n'),
    ]),
    ("A81", "ready answers 200 on a database error", "main.py", [
        ('        return error_response(503, "NOT_READY", "The database is not reachable. Try again shortly.")',
         '        return error_response(200, "NOT_READY", "The database is not reachable. Try again shortly.")'),
    ]),
    ("A82", "GET trip loses its consistent snapshot", "api/trips.py", [
        ("    return trips.read_trip(session, access.user, access.trip_id)",
         "    return trips.get_trip(session, access.user, access.trip_id)"),
    ]),
    ("A83", "refresh keeps its transaction open while asking the provider", "services/itinerary.py", [
        ("    session.rollback()\n    return item_id, offer\n", "    return item_id, offer\n"),
    ]),
    ("A84", "remove skips its first version check", "services/itinerary.py", [
        ("def remove_item(session, access, version, text):\n    _check_version(session, access, version)\n", "def remove_item(session, access, version, text):\n"),
    ]),
    ("A85", "reorder skips its first version check", "services/itinerary.py", [
        ("def reorder(session, access, version, item_ids):\n    _check_version(session, access, version)\n", "def reorder(session, access, version, item_ids):\n"),
    ]),
    ("A86", "refresh skips its first version check", "services/itinerary.py", [
        ("def _stored_quote(session, access, version, text):\n    _check_version(session, access, version)\n", "def _stored_quote(session, access, version, text):\n"),
    ]),
    ("A87", "travelers loses its upper bound", "api/schemas.py", [
        ("Travelers = Annotated[int, Field(strict=True, ge=1, le=20)]", "Travelers = Annotated[int, Field(strict=True, ge=1)]"),
    ]),
    ("A88", "search accepts a twenty-first destination", "services/search.py", [
        ("MAX_DESTINATIONS = 20", "MAX_DESTINATIONS = 21"),
    ]),
    ("A89", "invalid Retry-After waits one second by default", "providers/client.py", [
        ("DEFAULT_RATE_LIMIT_WAIT = 5  # A40: when Retry-After is absent or invalid", "DEFAULT_RATE_LIMIT_WAIT = 1  # mutation"),
    ]),
    ("A90", "search skips the past-start check", "services/search.py", [
        ("errors = date_errors(starts_on, ends_on, check_start=True)  # A36", "errors = date_errors(starts_on, ends_on, check_start=False)  # mutation"),
    ]),
    ("A91", "itinerary write routes fall back to trip_member", "api/itinerary.py", [
        ("trip_editor", "trip_member"),
    ]),
    ("A92", "trip update skips the version recheck before validation failure", "services/trips.py", [
        ("        if session.scalar(select(Trip.version).where(Trip.id == trip_id)) != version:\n"
         "            raise version_conflict(session, caller, trip_id)\n", ""),
    ]),
    ("A93", "trip list loses its consistent snapshot", "services/trips.py", [
        ("    return _snapshot_views(session, caller, Trip.id.in_(mine))",
         "    return trip_views(session, caller, Trip.id.in_(mine))"),
    ]),
    ("A94", "Retry-After accepts at most nine digits", "providers/client.py", [
        ('RETRY_AFTER = re.compile(r"[0-9]{1,4300}")', 'RETRY_AFTER = re.compile(r"[0-9]{1,9}")'),
    ]),
    # Iteration 5: documents.
    ("A95", "document fence drops the lease token", "services/documents.py", [
        (" AND lease_token=:token", ""),
    ]),
    ("A96", "expired document lease is never reclaimed", "services/documents.py", [
        ("WHERE state='waiting' OR (state='in_progress' AND lease_until < now())", "WHERE state='waiting'"),
    ]),
    ("A97", "live document lease is reclaimed", "services/documents.py", [
        ("lease_until < now()", "lease_until IS NOT NULL"),
    ]),
    ("A98", "download skips the re-hash", "services/documents.py", [
        ("if hashlib.sha256(content).hexdigest() != row.content_sha256:", "if False:"),
    ]),
    ("A99", "corrupt document is not marked failed", "services/documents.py", [
        ('.values(state="failed", content=None, content_sha256=None, completed_at=None, error=CORRUPT)',
         ".values(error=CORRUPT)"),
    ]),
    ("A100", "document html is not escaped", "services/documents.py", [
        ("    return html.escape(str(value), quote=True)", "    return str(value)"),
    ]),
    ("A101", "caller role leaks into the document source", "services/documents.py", [
        ('keys = ("id", "destinations", "startsOn", "endsOn", "budgetUsd", "travelers", "version")',
         'keys = ("id", "destinations", "startsOn", "endsOn", "budgetUsd", "travelers", "version", "role")'),
    ]),
    ("A102", "a random value leaks into the document source", "services/documents.py", [
        ('"items": source_items, "budget": budget(trip, items)}', '"items": source_items, "budget": budget(trip, items), "nonce": str(uuid4())}'),
    ]),
    ("A103", "repeated document requests are not deduplicated", "services/documents.py", [
        ("source_sha256=digest,", "source_sha256=uuid4().hex,"),
    ]),
    ("A104", "document request always reports created", "services/documents.py", [
        ("return created is not None, view(", "return True, view("),
    ]),
    ("A105", "fourth document attempt still renders", "services/documents.py", [
        ('if claim["attempts"] > MAX_ATTEMPTS:', "if False:"),
    ]),
    ("A106", "document render failure fails at once", "services/documents.py", [
        ('claim["attempts"] < MAX_ATTEMPTS', 'claim["attempts"] < 1'),
    ]),
    ("A107", "document retry keeps attempts", "services/documents.py", [
        ("attempts=0, ", ""),
    ]),
    ("A108", "document retry applies to any state", "services/documents.py", [
        (', Document.state == "failed")', ")"),
    ]),
    ("A109", "document render reads the clock", "services/documents.py", [
        ("{_e(_instant(document['requested_at']))}",
         "{_e(_instant(__import__('datetime').datetime.now(__import__('datetime').UTC)))}"),
    ]),
    ("A110", "document request answers 200 and 202 swapped", "api/documents.py", [
        ("    if not created:", "    if created:"),
    ]),
    ("A111", "document writes open to viewers", "api/documents.py", [
        ("trip_editor", "trip_member"),
    ]),
    ("A112", "document worker flag is ignored", "config.py", [
        ('DOCUMENT_WORKER_ENABLED = os.environ.get("DOCUMENT_WORKER_ENABLED", "1") != "0"', "DOCUMENT_WORKER_ENABLED = True"),
    ]),
    ("A113", "documents migration lacks the hash check", "db/migrations/versions/0004_documents.py", [
        ("    sa.CheckConstraint(\"content IS NULL OR content_sha256 = encode(sha256(content), 'hex')\", name='ck_documents_hash'),\n", ""),
    ]),
    ("A114", "document lookup drops its trip scope", "services/documents.py", [
        ("Document.id == document_id, Document.trip_id == access.trip_id", "Document.id == document_id"),
    ]),
    ("A115", "document insert stays inside the snapshot", "services/documents.py", [
        ("    finally:\n        session.rollback()\n", "    finally:\n        pass\n"),
    ]),
    ("A116", "document capture reads outside one snapshot", "services/documents.py", [
        ("    begin_snapshot(session)\n    try:", "    session.rollback()\n    try:"),
    ]),
    ("A117", "document id is not checked for its canonical form", "services/documents.py", [
        ("if str(document_id) == raw_id.lower():", "if True:"),
    ]),
    ("A118", "document download is served inline", "api/documents.py", [
        ("attachment; filename=", "inline; filename="),
    ]),
    ("A119", "document download drops its content security policy", "api/documents.py", [
        ("            \"content-security-policy\": \"default-src 'none'\",\n", ""),
    ]),
    ("A120", "document claim does not count attempts", "services/documents.py", [
        (", attempts=attempts+1", ""),
    ]),
    ("A121", "document lease length is ignored", "services/documents.py", [
        ('"lease": config.DOCUMENT_LEASE_SECONDS', '"lease": 30'),
    ]),
    ("A122", "document finish keeps its lease", "services/documents.py", [
        ('f"lease_token=NULL, lease_until=NULL {_FENCE}"', 'f"error=NULL {_FENCE}"'),
    ]),
    ("A123", "lifespan never starts the document worker", "main.py", [
        ("    if config.DOCUMENT_WORKER_ENABLED:\n", "    if False:\n"),
    ]),
    ("A124", "documents migration lacks the unique source", "db/migrations/versions/0004_documents.py", [
        ("    sa.UniqueConstraint('trip_id', 'source_sha256', name='uq_documents_source')\n", ""),
    ]),
    # Iteration 4: booking outcomes (docs/CONTRACT_NOTES.md 2.8), the monotonic rule and the one-open-attempt rule.
    ("A125", "a retry of a 400 is rejected", "domain/booking.py", [
        ('Outcome("rejected" if first else "unknown", None, refusal)', 'Outcome("rejected", None, refusal)'),
    ]),
    ("A126", "a booking POST answered 404 stays unknown", "domain/booking.py", [
        ('        case 404:\n            return Outcome("rejected", None, refusal)\n', '        case 404:\n            return Outcome("unknown", None, refusal)\n'),
    ]),
    ("A127", "OFFER_UNAVAILABLE no longer rejects the attempt", "domain/booking.py", [
        ('case 409 if body["code"] in ("OFFER_CHANGED", "OFFER_UNAVAILABLE"):', 'case 409 if body["code"] == "OFFER_CHANGED":'),
    ]),
    ("A128", "a 429 drops retryAfterSeconds", "domain/booking.py", [
        ('            extra = {"retryAfterSeconds": retry_after} if retry_after is not None else {}\n', '            extra = {}\n'),
    ]),
    ("A129", "REFERENCE_CONFLICT terms match on either id or revision", "domain/booking.py", [
        ('booking["offer"]["id"] == request["offerId"] and booking["offer"]["revision"]', 'booking["offer"]["id"] == request["offerId"] or booking["offer"]["revision"]'),
    ]),
    ("A130", "a pending answer may replace a terminal state", "domain/booking.py", [
        ('    "pending": OPEN,\n', '    "pending": (*OPEN, "confirmed", "failed", "rejected"),\n'),
    ]),
    ("A131", "an adopted booking keeps the stale lastError", "services/booking.py", [
        ('            values |= {"booking": outcome.booking, "last_error": None}\n', '            values |= {"booking": outcome.booking}\n'),
    ]),
    ("A132", "a pending attempt can be retried", "services/booking.py", [
        ('    if latest is None or latest.state != "unknown":\n', '    if latest is None or latest.state not in ("unknown", "pending"):\n'),
    ]),
    ("A133", "an open attempt blocks a new one only when confirmed", "services/booking.py", [
        ('    if latest is not None and latest.state not in CLOSED_WITHOUT_BOOKING:\n        raise _blocked(latest)\n', '    if latest is not None and latest.state == "confirmed":\n        raise _blocked(latest)\n'),
    ]),
    ("A134", "TERMS_CHANGED ignores the price", "services/booking.py", [
        ('    if revision != offer["revision"] or price != offer["price"]["amount"]:\n', '    if revision != offer["revision"]:\n'),
    ]),
    ("A135", "TERMS_CHANGED ignores the revision", "services/booking.py", [
        ('    if revision != offer["revision"] or price != offer["price"]["amount"]:\n', '    if price != offer["price"]["amount"]:\n'),
    ]),
    ("A136", "an unavailable snapshot is sent to the provider", "services/booking.py", [
        ('    if not offer["available"]:  # the application\'s expired label does not block: the provider decides (A28)\n', '    if False:  # mutation\n'),
    ]),
    ("A137", "the attempt is flushed, not committed, before the POST", "services/booking.py", [
        ('        session.commit()\n    except IntegrityError as error:\n', '        session.flush()\n    except IntegrityError as error:\n'),
    ]),
    ("A138", "a retry posts as a first attempt", "services/booking.py", [
        ('await _post(False, key, request)', 'await _post(True, key, request)'),
    ]),
    ("A139", "a by-reference 404 rejects an open attempt", "services/booking.py", [
        ('            outcome = adopt(reply.body)\n        retry_after = reply.retry_after', '            outcome = adopt(reply.body)\n        elif reply.status == 404:\n            outcome = Outcome("rejected", None, {"code": "NOT_FOUND", "message": "mutation"})\n        retry_after = reply.retry_after'),
    ]),
    ("A140", "an unreadable poll answer rejects an open attempt", "services/booking.py", [
        ('        pass  # the call line says why\n', '        outcome = Outcome("rejected", None, {"code": "PROVIDER_UNREACHABLE", "message": "mutation"})\n'),
    ]),
    ("A141", "REFERENCE_CONFLICT adopts a booking for any terms", "services/booking.py", [
        ('(code == "IDEMPOTENCY_CONFLICT" or same_terms(reply.body, request))', 'True'),
    ]),
    ("A142", "the poll interval is one step behind", "services/booking.py", [
        ('[LEAST(check_count + 2, :n)]', '[LEAST(check_count + 1, :n)]'),
    ]),
    ("A143", "the first poll uses the last interval", "services/booking.py", [
        ('timedelta(seconds=config.BOOKING_POLL_INTERVALS[0])', 'timedelta(seconds=config.BOOKING_POLL_INTERVALS[-1])'),
    ]),
    ("A144", "the poll sets no request id", "services/booking.py", [
        ('    request_id.set(f"poll-{uuid4().hex}")  # this coroutine runs as its own task, so the id is its own\n', ''),
    ]),
    ("A145", "the poll claims only unknown attempts", "services/booking.py", [
        ("WHERE state IN ('unknown', 'pending') AND next_check_at <= now()", "WHERE state IN ('unknown') AND next_check_at <= now()"),
    ]),
    ("A146", "a foreign key violation on the attempt insert is a 500", "services/booking.py", [
        ('        if isinstance(error.orig, ForeignKeyViolation):  # the item was deleted under us\n            raise not_found() from None\n', ''),
    ]),
    ("A147", "a 400 from the provider is not logged as our bug", "services/booking.py", [
        ('        logger.error("the provider refused our booking request as malformed, this is our bug: %s", request["clientReference"])\n', '        pass\n'),
    ]),
    ("A148", "an unresolved conflict is not logged", "services/booking.py", [
        ('    logger.error("booking conflict %s on %s could not be reconciled", code, request["clientReference"])\n', ''),
    ]),
    ("A149", "booking logs no line with its reference and request id", "services/booking.py", [
        ('    attempt_id, key, request = await run_in_threadpool(_open, session, access, text_id, revision, price)\n    logger.info("booking %s requestId=%s", request["clientReference"], request_id.get())\n', '    attempt_id, key, request = await run_in_threadpool(_open, session, access, text_id, revision, price)\n'),
    ]),
    ("A150", "retry logs no line with its reference and request id", "services/booking.py", [
        ('    attempt_id, key, request = await run_in_threadpool(_retryable, session, access, text_id)\n    logger.info("booking %s requestId=%s", request["clientReference"], request_id.get())\n', '    attempt_id, key, request = await run_in_threadpool(_retryable, session, access, text_id)\n'),
    ]),
    ("A151", "the poll logs no reconciling line", "services/booking.py", [
        ('    logger.info("reconciling %s requestId=%s", reference, request_id.get())\n', ''),
    ]),
    ("A152", "the reconciliation loop ignores BOOKING_POLL_ENABLED", "main.py", [
        ("    if config.BOOKING_POLL_ENABLED:\n", "    if True:\n"),
    ]),
    ("A153", "an error body mints its own request id", "main.py", [
        ('    identifier = request_id.get() or f"request-{uuid4().hex}"\n', '    identifier = f"request-{uuid4().hex}"\n'),
    ]),
    ("A154", "a booking for another clientReference is accepted", "providers/client.py", [
        ('        if booking["clientReference"] != reference:\n            raise ValueError("clientReference is not ours")\n', ''),
    ]),
    ("A155", "a timeout is logged as unreachable", "providers/client.py", [
        ('                outcome = "timeout"\n', '                outcome = "unreachable"\n'),
    ]),
    ("A156", "the provider request id is not logged", "providers/client.py", [
        ('            provider_request_id = body["requestId"]\n', '            provider_request_id = "-"\n'),
    ]),
    ("A157", "a provider 401 is not logged for the operator", "providers/client.py", [
        ('                logger.error("provider rejected our token requestId=%s", provider_request_id)\n', '                pass\n'),
    ]),
    ("A158", "removing an item deletes only its rejected attempts", "services/itinerary.py", [
        ('BookingAttempt.item_id == item_id, BookingAttempt.state.in_(CLOSED_WITHOUT_BOOKING)', 'BookingAttempt.item_id == item_id, BookingAttempt.state == "rejected"'),
    ]),
    ("A159", "a foreign key violation on item delete is a 500", "services/itinerary.py", [
        ('        if not isinstance(error.orig, ForeignKeyViolation):\n            raise\n', '        if True:\n            raise\n'),
    ]),
    ("A160", "the uq_itinerary_offer name check never matches", "services/itinerary.py", [
        ('error.orig.diag.constraint_name == "uq_itinerary_offer"', 'error.orig.diag.constraint_name == "uq_itinerary_offer_mutation"'),
    ]),
    ("A161", "a confirmed item is priced from the snapshot", "domain/itinerary.py", [
        ('        priced = item["booking"]["booking"]["offer"] if kind == "confirmed" else offer\n', '        priced = offer\n'),
    ]),
    ("A162", "a pending item is priced as confirmed", "domain/itinerary.py", [
        ('return "confirmed" if item.get("booking", {}).get("state") == "confirmed" else "estimate"', 'return "confirmed" if item.get("booking", {}).get("state") in ("confirmed", "pending") else "estimate"'),
    ]),
    ("A163", "an open or confirmed item stays bookable", "domain/itinerary.py", [
        ('item.get("booking", {}).get("state") in (*OPEN, "confirmed"):', 'item.get("booking", {}).get("state") in ():'),
    ]),
    ("A164", "a confirmed item raises PRICE_EXPIRED", "domain/itinerary.py", [
        ('if price_kind(item) == "estimate" and now >= parse_instant(item["offer"]["validUntil"]):', 'if now >= parse_instant(item["offer"]["validUntil"]):'),
    ]),
    ("A165", "a confirmed item raises UNAVAILABLE", "domain/itinerary.py", [
        ('if price_kind(item) == "estimate" and not item["offer"]["available"]:', 'if not item["offer"]["available"]:'),
    ]),
    ("A166", "booking_attempts cascades when an item is deleted", "db/migrations/versions/0003_booking_attempts.py", [
        ("ondelete='RESTRICT'", "ondelete='CASCADE'"),
    ]),
    ("A167", "uq_one_open_attempt counts a failed attempt as open", "db/migrations/versions/0003_booking_attempts.py", [
        ("state NOT IN ('rejected','failed')", "state NOT IN ('rejected')"),
    ]),
    ("A168", "the uq_itinerary_offer index is not created", "db/migrations/versions/0003_booking_attempts.py", [
        ("""    op.create_index('uq_itinerary_offer', 'itinerary_items', ['trip_id', sa.text("(offer->>'id')")], unique=True)\n""", ''),
    ]),
    ("A169", "a 429 without a valid Retry-After keeps a null retryAfterSeconds", "domain/booking.py", [
        ('{"retryAfterSeconds": retry_after} if retry_after is not None else {}', '{"retryAfterSeconds": retry_after}'),
    ]),
    ("A170", "a confirmed attempt answers BOOKING_IN_PROGRESS instead of ALREADY_BOOKED", "services/booking.py", [
        ('    if attempt.state == "confirmed":\n', '    if False:\n'),
    ]),
    ("A171", "a new attempt reuses attempt number 1", "services/booking.py", [
        ('    number = latest.attempt_no + 1 if latest else 1\n', '    number = 1\n'),
    ]),
    ("A172", "the attempt view shows a null booking", "domain/booking.py", [
        ('    if attempt.booking is not None:\n', '    if True:\n'),
    ]),
    ("A173", "the attempt view shows a null lastError", "domain/booking.py", [
        ('    if attempt.last_error is not None:\n', '    if True:\n'),
    ]),
    ("A174", "the attempt view shows a null lastCheckedAt", "domain/booking.py", [
        ('    if attempt.last_checked_at is not None:\n', '    if True:\n'),
    ]),
    ("A175", "a failed booking without failure breaks the view", "domain/booking.py", [
        ('attempt.booking.get("failure", {}).get("message", "The provider could not complete this booking.")', 'attempt.booking["failure"]["message"]'),
    ]),
    ("A176", "responses lose the X-Request-Id header", "main.py", [
        ('                MutableHeaders(scope=message).setdefault("X-Request-Id", identifier)\n', '                pass\n'),
    ]),
    ("A177", "the booking POST drops its Idempotency-Key header", "providers/client.py", [
        ('    headers = {"Content-Type": "application/json", "Idempotency-Key": key}\n', '    headers = {"Content-Type": "application/json"}\n'),
    ]),
    ("A178", "a provider booking's offer skips the amount and instant checks", "providers/client.py", [
        ('        _check_offer(booking["offer"])\n', ''),
    ]),
    ("A179", "the uq_one_open_attempt index is not created", "db/migrations/versions/0003_booking_attempts.py", [
        ("""    op.create_index('uq_one_open_attempt', 'booking_attempts', ['item_id'], unique=True, postgresql_where=sa.text("state NOT IN ('rejected','failed')"))\n""", ''),
    ]),
    ("A180", "the booking state CHECK is not created", "db/migrations/versions/0003_booking_attempts.py", [
        ("""    sa.CheckConstraint("state IN ('unknown','pending','confirmed','failed','rejected')", name='ck_booking_attempts_state'),\n""", ''),
    ]),
    ("A181", "the loser of the insert starts attempt 2 behind a winner that already closed", "services/booking.py", [
        ('        raise (_blocked(winner) if winner is not None else not_found()) from None\n',
         '        if winner is not None and winner.state in CLOSED_WITHOUT_BOOKING:\n            return _open(session, access, text_id, revision, price)\n        raise (_blocked(winner) if winner is not None else not_found()) from None\n'),
    ]),
    # The merge of Iterations 4 and 5: what a document shows and stores about a booking.
    ("A182", "document line shows the snapshot price", "services/documents.py", [
        ("{_e(amounts[item['id']])} USD", "{_e(offer['price']['amount'])} USD"),
    ]),
    ("A183", "document source stores the full attempt view", "services/documents.py", [
        ('{"state": item["booking"]["state"], "clientReference": item["booking"]["clientReference"]}', 'item["booking"]'),
    ]),
]


# The test written to catch each Iteration 4 mutation, by pytest node id under tests/. It runs first, alone: a kill costs one
# test instead of every mapped file up to the killer. Optional: an entry without one, or whose test passes or no longer
# exists, falls back to APP_TEST_MAP and then to the whole app suite, which is also where a FALSE KILL is still detected.
APP_KILLERS = {
    # Iteration 5: documents. The killer each one had in the Director's run.
    "A95": "test_app_documents.py::test_a_stolen_lease_fences_the_stale_workers_final_write_until_it_is_reclaimed",
    "A96": "test_app_documents.py::test_a_kill_mid_generation_is_recovered_by_the_next_worker_and_nothing_reports_complete_early",
    "A97": "test_app_documents.py::test_a_stolen_lease_fences_the_stale_workers_final_write_until_it_is_reclaimed",
    "A98": "test_app_documents.py::test_a_corrupted_file_is_never_sent_marks_the_document_failed_and_retry_restores_it",
    "A99": "test_app_documents.py::test_a_corrupted_file_is_never_sent_marks_the_document_failed_and_retry_restores_it",
    "A100": "test_app_documents.py::test_content_has_every_required_text_and_escapes_user_and_provider_text",
    "A101": "test_app_documents.py::test_content_has_every_required_text_and_escapes_user_and_provider_text",
    "A102": "test_app_documents.py::test_content_has_every_required_text_and_escapes_user_and_provider_text",
    "A103": "test_app_documents.py::test_content_has_every_required_text_and_escapes_user_and_provider_text",
    "A104": "test_app_documents.py::test_repeated_requests_return_the_same_document_even_when_24_arrive_at_once",
    "A105": "test_app_documents.py::test_a_claim_that_makes_the_fourth_attempt_fails_the_document_without_rendering_it",
    "A106": "test_app_documents.py::test_an_unrenderable_source_fails_after_three_attempts_and_retry_only_applies_to_failed",
    "A107": "test_app_documents.py::test_an_unrenderable_source_fails_after_three_attempts_and_retry_only_applies_to_failed",
    "A108": "test_app_documents.py::test_an_unrenderable_source_fails_after_three_attempts_and_retry_only_applies_to_failed",
    "A109": "test_app_documents.py::test_content_has_every_required_text_and_escapes_user_and_provider_text",
    "A110": "test_app_documents.py::test_request_waits_without_a_worker_survives_a_hard_kill_then_completes_with_a_matching_hash",
    "A111": "test_app_documents.py::test_viewer_reads_but_cannot_write_outsider_and_bad_ids_get_the_missing_trip_404",
    "A112": "test_app_documents.py::test_request_waits_without_a_worker_survives_a_hard_kill_then_completes_with_a_matching_hash",
    "A113": "test_app_documents.py::test_a_corrupted_file_is_never_sent_marks_the_document_failed_and_retry_restores_it",
    "A114": "test_app_documents.py::test_viewer_reads_but_cannot_write_outsider_and_bad_ids_get_the_missing_trip_404",
    "A115": "test_app_documents.py::test_repeated_requests_return_the_same_document_even_when_24_arrive_at_once",
    "A116": "test_app_documents.py::test_the_source_pairs_a_version_with_its_own_items_when_an_edit_commits_mid_capture",
    "A117": "test_app_documents.py::test_viewer_reads_but_cannot_write_outsider_and_bad_ids_get_the_missing_trip_404",
    "A118": "test_app_documents.py::test_request_waits_without_a_worker_survives_a_hard_kill_then_completes_with_a_matching_hash",
    "A119": "test_app_documents.py::test_request_waits_without_a_worker_survives_a_hard_kill_then_completes_with_a_matching_hash",
    "A120": "test_app_documents.py::test_request_waits_without_a_worker_survives_a_hard_kill_then_completes_with_a_matching_hash",
    "A121": "test_app_documents.py::test_a_kill_mid_generation_is_recovered_by_the_next_worker_and_nothing_reports_complete_early",
    "A122": "test_app_documents.py::test_a_stolen_lease_fences_the_stale_workers_final_write_until_it_is_reclaimed",
    "A123": "test_app_documents.py::test_request_waits_without_a_worker_survives_a_hard_kill_then_completes_with_a_matching_hash",
    "A124": "test_app_documents.py::test_request_waits_without_a_worker_survives_a_hard_kill_then_completes_with_a_matching_hash",
    # Iteration 4: bookings.
    "A125": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[400-retry]",
    "A126": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[404-first]",
    "A127": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[409-offer-unavailable-first]",
    "A128": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[429-first]",
    "A129": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[reference-conflict-other-revision-first]",
    "A130": "test_app_booking_recovery.py::test_late_pending_post_answer_never_undoes_a_confirmation_the_poller_stored",
    "A131": "test_app_booking_views.py::test_last_error_is_cleared_when_a_retry_adopts_a_booking",
    "A132": "test_app_bookings.py::test_retry_of_a_pending_attempt_is_nothing_to_retry_and_sends_no_post",
    "A133": "test_app_booking_views.py::test_an_open_or_confirmed_attempt_is_refused_before_the_terms_are_compared[unknown]",
    "A134": "test_app_bookings.py::test_changed_terms_and_unavailable_snapshot_make_no_provider_post",
    "A135": "test_app_bookings.py::test_changed_terms_and_unavailable_snapshot_make_no_provider_post",
    "A136": "test_app_bookings.py::test_changed_terms_and_unavailable_snapshot_make_no_provider_post",
    "A137": "test_app_booking_recovery.py::test_crash_while_post_is_unanswered_keeps_unknown_and_retry_resends_same_key_and_body",
    "A138": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[400-retry]",
    "A139": "test_app_booking_recovery.py::test_reads_that_prove_nothing_never_change_an_unknown_attempt[404]",
    "A140": "test_app_booking_recovery.py::test_reads_that_prove_nothing_never_change_an_unknown_attempt[non-json]",
    "A141": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[reference-conflict-other-offer-first]",
    "A142": "test_app_booking_recovery.py::test_poll_schedule_follows_the_configured_intervals",
    "A143": "test_app_booking_recovery.py::test_poll_schedule_follows_the_configured_intervals",
    "A144": "test_app_diagnostics.py::test_the_background_loop_logs_its_own_poll_request_id",
    "A145": "test_app_booking_recovery.py::test_pending_booking_reaches_its_outcome_with_no_user_action[pending-to-confirmed]",
    "A146": "test_app_booking_concurrency.py::test_item_deleted_while_the_attempt_insert_is_in_flight_makes_the_booking_a_404",
    "A147": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[400-first]",
    "A148": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[idempotency-conflict-missing-first]",
    "A149": "test_app_diagnostics.py::test_the_background_loop_logs_its_own_poll_request_id",
    "A150": "test_app_diagnostics.py::test_the_background_loop_logs_its_own_poll_request_id",
    "A151": "test_app_diagnostics.py::test_the_background_loop_logs_its_own_poll_request_id",
    "A152": "test_app_booking_recovery.py::test_disabled_poller_changes_nothing_and_the_same_setup_converges_when_enabled",
    "A153": "test_app_diagnostics.py::test_a_provider_failure_on_add_logs_the_error_body_request_id_the_client_sees",
    "A154": "test_app_booking_views.py::test_a_booking_that_breaks_the_contract_or_our_reference_leaves_the_attempt_unknown[wrong-reference]",
    "A155": "test_app_diagnostics.py::test_each_provider_call_logs_exactly_one_line_with_both_request_ids[timeout]",
    "A156": "test_app_diagnostics.py::test_each_provider_call_logs_exactly_one_line_with_both_request_ids[provider-error-with-its-request-id]",
    "A157": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[401-first]",
    "A158": "test_app_booking_concurrency.py::test_delete_is_refused_while_an_attempt_is_open_or_confirmed_and_allowed_once_closed[failed]",
    "A159": "test_app_booking_concurrency.py::test_concurrent_delete_and_book_never_both_succeed_or_leave_an_orphan",
    "A160": "test_app_bookings.py::test_same_offer_cannot_be_added_twice",
    "A161": "test_app_bookings.py::test_confirmed_budget_uses_accepted_price_and_mixed_outcomes_remain_visible",
    "A162": "test_app_booking_recovery.py::test_pending_booking_reaches_its_outcome_with_no_user_action[pending-to-confirmed]",
    "A163": "test_app_booking_views.py::test_attempt_view_omits_instead_of_null_and_explains_the_next_step[unknown]",
    "A164": "test_app_booking_views.py::test_a_confirmed_item_raises_no_expired_or_unavailable_issue_but_an_estimate_does",
    "A165": "test_app_booking_views.py::test_a_confirmed_item_raises_no_expired_or_unavailable_issue_but_an_estimate_does",
    "A166": "test_app_migration_0003.py::test_attempt_foreign_key_to_the_item_is_restrict_and_blocks_the_delete",
    "A167": "test_app_migration_0003.py::test_uq_one_open_attempt_allows_a_new_attempt_after_rejected_or_failed[failed]",
    "A168": "test_app_migration_0003.py::test_uq_itinerary_offer_refuses_the_same_offer_twice_in_one_trip_only",
    "A169": "test_app_bookings.py::test_rate_limit_without_a_valid_retry_after_omits_retry_after_seconds[absent]",
    "A170": "test_app_bookings.py::test_closed_attempt_controls_new_attempt_and_terminal_retry[confirmed]",
    "A171": "test_app_bookings.py::test_closed_attempt_controls_new_attempt_and_terminal_retry[rejected]",
    "A172": "test_app_booking_views.py::test_attempt_view_omits_instead_of_null_and_explains_the_next_step[unknown]",
    "A173": "test_app_booking_views.py::test_attempt_view_omits_instead_of_null_and_explains_the_next_step[pending]",
    "A174": "test_app_booking_views.py::test_attempt_view_omits_instead_of_null_and_explains_the_next_step[unknown]",
    "A175": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[201-failed-without-failure-first]",
    "A176": "test_app_diagnostics.py::test_every_success_carries_a_fresh_request_id_and_errors_echo_it",
    "A177": "test_app_bookings.py::test_every_post_outcome_keeps_identity_and_expected_state[201-confirmed-retry]",
    "A178": "test_app_booking_views.py::test_a_booking_that_breaks_the_contract_or_our_reference_leaves_the_attempt_unknown[newline-amount]",
    "A179": "test_app_migration_0003.py::test_uq_one_open_attempt_refuses_a_second_attempt_while_one_is_open_or_confirmed[unknown]",
    "A180": "test_app_migration_0003.py::test_attempt_state_check_refuses_an_unknown_state_and_accepts_the_five_real_ones",
    "A181": "test_app_booking_concurrency.py::test_concurrent_bookings_never_post_twice_when_the_winner_closes_at_once[failed]",
    "A182": "test_app_documents.py::test_a_confirmed_booking_shows_its_state_and_accepted_price_and_stores_only_state_and_reference",
    "A183": "test_app_documents.py::test_two_requests_either_side_of_a_poll_return_the_same_document",
}


APP_TEST_MAP = {
    "api/deps.py": ("test_app_permissions.py", "test_app_itinerary_permissions.py"),
    "api/documents.py": ("test_app_documents.py",),
    "config.py": ("test_app_documents.py",),
    "services/documents.py": ("test_app_documents.py",),
    "db/migrations/versions/0004_documents.py": ("test_app_documents.py", "test_app_database.py"),
    "api/itinerary.py": ("test_app_itinerary_permissions.py", "test_app_bookings.py"),
    "context.py": ("test_app_diagnostics.py",),
    "db/attempts.py": ("test_app_bookings.py", "test_app_booking_concurrency.py"),
    "domain/booking.py": ("test_app_bookings.py", "test_app_booking_views.py", "test_app_booking_recovery.py"),
    "services/booking.py": (
        "test_app_bookings.py",
        "test_app_booking_views.py",
        "test_app_booking_recovery.py",
        "test_app_booking_concurrency.py",
        "test_app_diagnostics.py",
    ),
    "api/participants.py": ("test_app_permissions.py",),
    "api/schemas.py": ("test_app_validation.py", "test_app_search_itinerary_validation.py"),
    "api/trips.py": ("test_app_permissions.py", "test_app_transactions.py"),
    "db/migrations/versions/0002_itinerary_and_party.py": (
        "test_app_database.py",
        "test_app_database_constraints.py",
    ),
    "db/migrations/versions/0003_booking_attempts.py": ("test_app_migration_0003.py", "test_app_booking_concurrency.py"),
    "db/models.py": ("test_app_database.py", "test_app_database_constraints.py"),
    "domain/itinerary.py": ("test_app_itinerary.py", "test_app_bookings.py", "test_app_booking_views.py", "test_app_documents.py"),
    "domain/rules.py": ("test_app_validation.py",),
    "main.py": (
        "test_app_errors.py",
        "test_app_permissions.py",
        "test_app_diagnostics.py",
        "test_app_booking_recovery.py",
        "test_app_documents.py",
    ),
    "money.py": ("test_app_validation.py", "test_app_itinerary.py"),
    "providers/client.py": ("test_app_provider.py", "test_app_bookings.py", "test_app_booking_views.py", "test_app_diagnostics.py"),
    "services/itinerary.py": (
        "test_app_itinerary.py",
        "test_app_concurrency.py",
        "test_app_bookings.py",
        "test_app_booking_concurrency.py",
        "test_app_documents.py",
    ),
    "services/participants.py": ("test_app_permissions.py", "test_app_transactions.py"),
    "services/search.py": (
        "test_app_provider.py",
        "test_app_search_itinerary_validation.py",
        "test_app_concurrency.py",
    ),
    "services/trips.py": (
        "test_app_transactions.py",
        "test_app_validation.py",
        "test_app_permissions.py",
    ),
}


def run(number, label, filename, replacements, suite="mock"):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workspace:
        copy = Path(workspace)
        source_dir = "app" if suite == "app" else "provider-mock"
        shutil.copytree(ROOT / source_dir, copy / source_dir, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "contracts", copy / "contracts")
        target = copy / source_dir / filename
        source = target.read_text(encoding="utf-8")
        for old, new in replacements:
            if old not in source:
                return "STALE", ""
            source = source.replace(old, new)
        target.write_text(source, encoding="utf-8")
        variable = "APP_SOURCE_ROOT" if suite == "app" else "MOCK_APP_DIR"
        source_root = copy if suite == "app" else copy / source_dir

        def pytest(tests):
            return subprocess.run(
                [sys.executable, "-m", "pytest", *tests, "-x", "-q", "--no-header", "-p", "no:cacheprovider"],
                env={**os.environ, variable: str(source_root)},
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        if suite == "app":
            all_tests = [str(path) for path in sorted((ROOT / "tests").glob("test_app_*.py"))]
            mapped_tests = [str(ROOT / "tests" / name) for name in APP_TEST_MAP.get(filename, ())]
            result = None
            if number in APP_KILLERS:  # the one test written for this mutation, run alone and first
                result = pytest([str(ROOT / "tests") + os.sep + APP_KILLERS[number]])
                if not re.search(r"^FAILED tests[/\\]", result.stdout, re.MULTILINE):
                    result = None  # it passed, or did not run (renamed, or an error): the mapped files decide, and judge a false kill
            if result is None:
                result = pytest(mapped_tests or all_tests)
                if mapped_tests and result.returncode == 0:
                    result = pytest(all_tests)
        else:
            result = pytest([str(ROOT / "tests"), "--ignore-glob=*test_app_*.py"])
    if result.returncode == 0:
        return "SURVIVED", ""
    failed = re.search(r"^FAILED tests[/\\](\S+)", result.stdout, re.MULTILINE)
    if failed:
        return "killed", failed.group(1)
    errored = re.search(r"^ERROR tests[/\\](\S+)", result.stdout, re.MULTILINE)
    return "FALSE KILL", errored.group(1) if errored else f"the {suite} did not start"


def main():
    only = sys.argv[1:]  # mutation numbers, words from labels, or "app"
    app_only = "app" in only
    only = [word for word in only if word != "app"]
    results = []
    total_seconds = 0.0
    suites = [("app", APP_MUTATIONS)] if app_only else [("mock", MOCK_MUTATIONS), ("app", APP_MUTATIONS)]
    for suite, mutations in suites:
        for number, label, filename, replacements in mutations:
            if only and not any(word == number or word in label for word in only):
                continue
            started = time.monotonic()
            outcome, killer = run(number, label, filename, replacements, suite)
            elapsed = time.monotonic() - started
            total_seconds += elapsed
            results.append(outcome)
            print(f"{outcome:9} {number:>3}  {label}  <-  {killer}  ({elapsed:.1f}s)", flush=True)
    bad = len(results) - results.count("killed")
    print(f"\n{results.count('killed')}/{len(results)} mutations killed, {bad} not killed in {total_seconds:.1f}s")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
