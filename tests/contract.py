"""Checks a provider response against contracts/openAPI.json and its prose-only rules.

The schema validates shape, not rules between fields, so each prose rule is asserted
here as well. Requires rfc3339-validator: without it, date-time formats are not checked.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "contracts" / "openAPI.json"
SPEC = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def schema_errors(schema_name, body):
    schema = {"$ref": f"#/components/schemas/{schema_name}", "components": SPEC["components"]}
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return [error.message for error in validator.iter_errors(body)]


def instant(value):
    return datetime.fromisoformat(value)


def check_offer_rules(offer):
    assert instant(offer["endsAt"]) > instant(offer["startsAt"]), "endsAt must be later than startsAt"
    if offer["category"] == "transport":
        assert "origin" in offer, "transport offers carry origin (A30)"


def check_booking_rules(booking):
    check_offer_rules(booking["offer"])
    assert ("failure" in booking) == (booking["status"] == "failed"), "failure only on failed bookings"
    assert instant(booking["updatedAt"]) >= instant(booking["createdAt"]), "updatedAt before createdAt"


def check_search_rules(result):
    for offer in result["offers"]:
        check_offer_rules(offer)
    assert result["partial"] == bool(result["warnings"]), "warnings must be non-empty exactly when partial"


PROSE_RULES = {"Offer": check_offer_rules, "Booking": check_booking_rules, "SearchResponse": check_search_rules}


def find_operation(method, path):
    for template, item in SPEC["paths"].items():
        pattern = re.sub(r"\{[^}]+\}", "[^/]+", template)
        if method.lower() in item and re.fullmatch(pattern, path):
            return item[method.lower()]
    raise AssertionError(f"{method} {path} is not in the contract")


def declared_schema(operation, status):
    response = operation["responses"].get(str(status))
    assert response is not None, f"{operation['operationId']} does not declare {status}"
    if "$ref" in response:
        response = SPEC["components"]["responses"][response["$ref"].rsplit("/", 1)[1]]
    return response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]


def check_response(method, path, status, headers, raw_body):
    """Raises AssertionError on any violation. Returns the operationId."""
    operation = find_operation(method, path)
    schema_name = declared_schema(operation, status)
    assert headers["content-type"].startswith("application/json")
    body = json.loads(raw_body)
    errors = schema_errors(schema_name, body)
    assert not errors, f"{operation['operationId']} {status} violates {schema_name}: {errors}"
    PROSE_RULES.get(schema_name, lambda _body: None)(body)
    if status == 429:
        retry_after = headers.get("retry-after", "")
        assert retry_after.isdigit() and int(retry_after) >= 1, "429 needs Retry-After in whole seconds (A40)"
    return operation["operationId"]


def declared_responses():
    return {
        (operation["operationId"], int(status))
        for item in SPEC["paths"].values()
        for operation in item.values()
        for status in operation["responses"]
    }
