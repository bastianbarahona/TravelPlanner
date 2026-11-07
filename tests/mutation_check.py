"""Proves the suite can fail. Not collected by pytest. Run: python tests/mutation_check.py

Each mutation breaks one rule in a temporary copy of the mock, then expects pytest to
fail. A survivor is a rule no test protects. A stale entry means the source changed
and the mutation no longer applies. The first column is the required test number in
provider-mock/README.md, or a dash for rules beyond those ten.
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

MUTATIONS = [
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


def run(number, label, filename, replacements):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workspace:
        copy = Path(workspace)
        shutil.copytree(ROOT / "provider-mock", copy / "provider-mock", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "contracts", copy / "contracts")
        target = copy / "provider-mock" / filename
        source = target.read_text(encoding="utf-8")
        for old, new in replacements:
            if old not in source:
                return "STALE", ""
            source = source.replace(old, new)
        target.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(ROOT / "tests"), "-x", "-q", "--no-header", "-p", "no:cacheprovider"],
            env={**os.environ, "MOCK_APP_DIR": str(copy / "provider-mock")},
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    if result.returncode == 0:
        return "SURVIVED", ""
    match = re.search(r"^(?:FAILED|ERROR) tests[/\\](\S+)", result.stdout, re.MULTILINE)
    return "killed", match.group(1) if match else "the mock did not start"


def main():
    only = sys.argv[1:]  # test numbers, or words from a mutation's label
    results = []
    for number, label, filename, replacements in MUTATIONS:
        if only and not any(word == number or word in label for word in only):
            continue
        outcome, killer = run(number, label, filename, replacements)
        results.append(outcome)
        print(f"{outcome:9} {number:>2}  {label}  <-  {killer}", flush=True)
    bad = len(results) - results.count("killed")
    print(f"\n{results.count('killed')}/{len(results)} mutations killed, {bad} not killed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
