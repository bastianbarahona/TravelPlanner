"""Proves the suites can fail. Not collected by pytest. Run: python tests/mutation_check.py

Each mutation breaks one rule in a temporary source copy, then expects pytest to fail.
A survivor is a rule no test protects. A stale entry means the source changed and the
mutation no longer applies. Mock numbers come from provider-mock/README.md; app
mutations are A01 onward. Pass ``app`` to run only app mutations.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
    ("A08", "trip list stops filtering by caller", "services/trips.py", [('    return trip_views(session, caller, Trip.id.in_(mine))', '    return trip_views(session, caller)')]),
    ("A09", "canonical UUID check is removed", "api/deps.py", [('    if str(trip_id) != tripId.lower():  # UUID() also takes braces, urn: and underscores\n        raise not_found()\n', '')]),
    ("A10", "organizer rows can be changed or removed", "services/participants.py", [('    if role == "organizer":\n', '    if False:\n')]),
    ("A11", "known_user accepts an unknown identity", "api/deps.py", [('    if userId not in IDENTITIES:\n', '    if False:\n')]),
    ("A12", "health route disappears from the schema", "main.py", [('@app.get("/health")', '@app.get("/health", include_in_schema=False)')]),
    # Money boundaries and representation.
    ("A13", "amount loses its integer digit bound", "money.py", [('(0|[1-9][0-9]{0,9})', '(0|[1-9][0-9]*)')]),
    ("A14", "amount accepts one decimal", "money.py", [('[0-9]{2}")', '[0-9]{1,2}")')]),
    ("A15", "amount accepts a leading zero", "money.py", [('r"(0|[1-9][0-9]{0,9})\\.[0-9]{2}"', 'r"[0-9]{1,10}\\.[0-9]{2}"')]),
    ("A16", "amount validation uses match instead of fullmatch", "money.py", [('AMOUNT.fullmatch(text)', 'AMOUNT.match(text)')]),
    ("A17", "formatted amount loses its two decimals", "money.py", [('    return f"{amount:.2f}"', '    return str(amount).rstrip("0").rstrip(".")')]),
    ("A18", "a truth test skips a zero budget patch", "services/trips.py", [('    if "budget_usd" in changes:\n', '    if parse_amount(changes.get("budget_usd", "0.00")):\n')]),
    # Optimistic versioning and participant concurrency.
    ("A19", "version leaves the guarded UPDATE", "services/trips.py", [('where(Trip.id == trip_id, Trip.version == version)', 'where(Trip.id == trip_id)')]),
    ("A20", "trip version increment is removed", "services/trips.py", [('version=Trip.version + 1', 'version=version')]),
    ("A21", "zero rowcount is ignored", "services/trips.py", [('    if updated.rowcount == 0:\n        raise version_conflict(session, caller, trip_id)\n', '')]),
    ("A22", "destinations are replaced before the version guard", "services/trips.py", [(DESTINATIONS_AFTER_GUARD, DESTINATIONS_BEFORE_GUARD)]),
    ("A23", "version conflict current state is empty", "services/trips.py", [('{"current": get_trip(session, caller, trip_id)}', '{"current": {}}')]),
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
]


def run(number, label, filename, replacements, suite="mock"):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workspace:
        copy = Path(workspace)
        source_dir = "app" if suite == "app" else "provider-mock"
        shutil.copytree(ROOT / source_dir, copy / source_dir, ignore=shutil.ignore_patterns("__pycache__"))
        if suite == "mock":
            shutil.copytree(ROOT / "contracts", copy / "contracts")
        target = copy / source_dir / filename
        source = target.read_text(encoding="utf-8")
        for old, new in replacements:
            if old not in source:
                return "STALE", ""
            source = source.replace(old, new)
        target.write_text(source, encoding="utf-8")
        tests = (
            [str(path) for path in sorted((ROOT / "tests").glob("test_app_*.py"))]
            if suite == "app"
            else [str(ROOT / "tests"), "--ignore-glob=*test_app_*.py"]
        )
        variable = "APP_SOURCE_ROOT" if suite == "app" else "MOCK_APP_DIR"
        source_root = copy if suite == "app" else copy / source_dir
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *tests, "-x", "-q", "--no-header", "-p", "no:cacheprovider"],
            env={**os.environ, variable: str(source_root)},
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    if result.returncode == 0:
        return "SURVIVED", ""
    match = re.search(r"^(?:FAILED|ERROR) tests[/\\](\S+)", result.stdout, re.MULTILINE)
    return "killed", match.group(1) if match else f"the {suite} did not start"


def main():
    only = sys.argv[1:]  # mutation numbers, words from labels, or "app"
    app_only = "app" in only
    only = [word for word in only if word != "app"]
    results = []
    suites = [("app", APP_MUTATIONS)] if app_only else [("mock", MOCK_MUTATIONS), ("app", APP_MUTATIONS)]
    for suite, mutations in suites:
        for number, label, filename, replacements in mutations:
            if only and not any(word == number or word in label for word in only):
                continue
            outcome, killer = run(number, label, filename, replacements, suite)
            results.append(outcome)
            print(f"{outcome:9} {number:>3}  {label}  <-  {killer}", flush=True)
    bad = len(results) - results.count("killed")
    print(f"\n{results.count('killed')}/{len(results)} mutations killed, {bad} not killed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
