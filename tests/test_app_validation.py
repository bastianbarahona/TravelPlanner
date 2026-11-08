"""Requirements 2 and 3: validation, date rules and exact money persistence."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app_support import (
    app_client as make_app_client,
    assert_validation_error,
    create_trip,
    raw_json_request,
    valid_trip_body,
)


@pytest.mark.parametrize(
    "destinations,field",
    [
        ([], "destinations"),
        (["   "], "destinations[0]"),
        (["\u00a0\u00a0"], "destinations[0]"),
        (["x" * 201], "destinations[0]"),
        (["Lis\x00bon"], "destinations[0]"),
        (["\ud800"], "destinations[0]"),
        (["Lisbon", "   "], "destinations[1]"),
    ],
    ids=["empty", "spaces", "nbsp", "over-200", "nul", "lone-surrogate", "second-item"],
)
def test_invalid_destination_is_actionable_and_atomic(app_client, database_counts, destinations, field):
    response = raw_json_request(app_client, "POST", "/trips", valid_trip_body(destinations=destinations))
    sent = destinations[int(field.removeprefix("destinations[").removesuffix("]"))] if "[" in field else None
    sent = sent if sent is not None and len(sent) >= 6 else None
    assert_validation_error(response, field, sent=sent)
    assert database_counts() == (0, 0, 0)


def test_missing_destinations_is_actionable_and_atomic(app_client, database_counts):
    body = valid_trip_body()
    del body["destinations"]
    response = app_client.post("/trips", json=body)
    assert_validation_error(response, "destinations")
    assert database_counts() == (0, 0, 0)


@pytest.mark.parametrize(
    "budget",
    [
        420.1,
        "420.1",
        "420",
        "420.100",
        "00.10",
        "-1.00",
        "1E+3",
        "420.10\n",
        " 12.34",
        None,
        "\u0664\u0662\u0660.\u0661\u0660",
        "10000000000.00",
    ],
    ids=[
        "json-number",
        "one-decimal",
        "no-decimals",
        "three-decimals",
        "leading-zero",
        "negative",
        "exponent",
        "newline",
        "leading-space",
        "null",
        "non-ascii-digits",
        "numeric-overflow",
    ],
)
def test_invalid_budget_is_actionable_and_atomic(app_client, database_counts, budget):
    response = raw_json_request(app_client, "POST", "/trips", valid_trip_body(budgetUsd=budget))
    sent = budget if budget is not None and len(str(budget)) >= 6 else None
    assert_validation_error(response, "budgetUsd", sent=sent)
    assert database_counts() == (0, 0, 0)


@pytest.mark.parametrize(
    "bad_date",
    [1893456000, "2030-01-02T00:00:00", "20300102", "2030-02-30", "2030-1-2", None],
    ids=["unix-number", "datetime", "compact", "impossible", "un-padded", "null"],
)
@pytest.mark.parametrize("field", ["startsOn", "endsOn"])
def test_invalid_date_is_actionable_and_atomic(app_client, database_counts, bad_date, field):
    response = raw_json_request(app_client, "POST", "/trips", valid_trip_body(**{field: bad_date}))
    assert_validation_error(response, field)
    assert database_counts() == (0, 0, 0)


def test_reversed_dates_and_past_start_save_nothing(app_client, database_counts):
    body = valid_trip_body()
    reversed_dates = app_client.post(
        "/trips", json=body | {"startsOn": body["endsOn"], "endsOn": body["startsOn"]}
    )
    assert_validation_error(reversed_dates, "body")
    assert database_counts() == (0, 0, 0)

    too_old = datetime.now(timezone.utc).date() - timedelta(days=2)
    past = app_client.post(
        "/trips",
        json=valid_trip_body(startsOn=too_old.isoformat(), endsOn=(too_old + timedelta(days=3)).isoformat()),
    )
    assert_validation_error(past, "startsOn")
    assert database_counts() == (0, 0, 0)


@pytest.mark.parametrize("extra", [{"organizerId": "cam"}, {"userId": "cam"}])
def test_create_refuses_identity_and_unknown_fields(app_client, database_counts, extra):
    response = app_client.post("/trips", json=valid_trip_body() | extra)
    assert_validation_error(response, "body")
    assert "cam" not in response.text
    assert database_counts() == (0, 0, 0)


def test_accepted_boundaries_are_preserved_exactly(app_client):
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    destinations = [
        "  LiSbOn  ",
        f"  {'X' * 200}  ",
        "Lisbon",
        "Porto",
        "Lisbon",
        "\u00e9" * 200,
    ]
    response = app_client.post(
        "/trips",
        json=valid_trip_body(
            destinations=destinations,
            startsOn=yesterday.isoformat(),
            endsOn=yesterday.isoformat(),
            budgetUsd="0.00",
        ),
    )
    assert response.status_code == 201
    trip = response.json()
    assert trip["destinations"] == [
        "LiSbOn",
        "X" * 200,
        "Lisbon",
        "Porto",
        "Lisbon",
        "\u00e9" * 200,
    ]
    assert trip["startsOn"] == yesterday.isoformat()
    assert trip["endsOn"] == yesterday.isoformat()
    assert trip["budgetUsd"] == "0.00"

    maximum = create_trip(app_client, budgetUsd="9999999999.99")
    assert maximum["budgetUsd"] == "9999999999.99"


@pytest.mark.parametrize("version", ["1", 1.0, True], ids=["string", "float", "boolean"])
def test_patch_version_is_a_strict_integer(app_client, version):
    trip = create_trip(app_client)
    response = app_client.patch(f"/trips/{trip['id']}", json={"version": version, "budgetUsd": "1.00"})
    assert_validation_error(response, "version")
    assert app_client.get(f"/trips/{trip['id']}").json() == trip


@pytest.mark.parametrize(
    "body,field",
    [
        ({"budgetUsd": "1.00"}, "version"),
        ({"version": 1, "unexpected": "value"}, "body"),
    ],
    ids=["missing-version", "unknown-field"],
)
def test_patch_requires_version_and_refuses_unknown_fields(app_client, body, field):
    trip = create_trip(app_client)
    response = app_client.patch(f"/trips/{trip['id']}", json=body)
    assert_validation_error(response, field)
    assert app_client.get(f"/trips/{trip['id']}").json() == trip


@pytest.mark.parametrize(
    "change,field",
    [
        ({"destinations": None}, "destinations"),
        ({"startsOn": None}, "startsOn"),
        ({"endsOn": None}, "endsOn"),
        ({"budgetUsd": None}, "budgetUsd"),
    ],
)
def test_patch_refuses_explicit_null(app_client, change, field):
    trip = create_trip(app_client)
    response = app_client.patch(f"/trips/{trip['id']}", json={"version": 1} | change)
    assert_validation_error(response, field)
    assert app_client.get(f"/trips/{trip['id']}").json() == trip


def test_patch_requires_a_change_and_validates_merged_dates(app_client):
    trip = create_trip(app_client)
    no_change = app_client.patch(f"/trips/{trip['id']}", json={"version": 1})
    assert_validation_error(no_change, "body")

    bad_end = (datetime.fromisoformat(trip["startsOn"]).date() - timedelta(days=1)).isoformat()
    bad_end = app_client.patch(f"/trips/{trip['id']}", json={"version": 1, "endsOn": bad_end})
    assert_validation_error(bad_end, "body")

    bad_start = (datetime.fromisoformat(trip["endsOn"]).date() + timedelta(days=1)).isoformat()
    bad_start = app_client.patch(
        f"/trips/{trip['id']}", json={"version": 1, "startsOn": bad_start}
    )
    assert_validation_error(bad_start, "body")
    assert app_client.get(f"/trips/{trip['id']}").json() == trip


@pytest.mark.parametrize("tz", ["XXX-14", "XXX+12"])
def test_past_start_rule_uses_utc_in_extreme_local_timezones(start_app, tz):
    today = datetime.now(timezone.utc).date()
    process = start_app(TZ=tz)
    with make_app_client(process.url) as client:
        yesterday = today - timedelta(days=1)
        accepted = client.post(
            "/trips",
            json=valid_trip_body(startsOn=yesterday.isoformat(), endsOn=yesterday.isoformat()),
        )
        assert accepted.status_code == 201

        two_days_ago = today - timedelta(days=2)
        refused = client.post(
            "/trips",
            json=valid_trip_body(
                startsOn=two_days_ago.isoformat(), endsOn=two_days_ago.isoformat()
            ),
        )
        assert_validation_error(refused, "startsOn")


def test_budget_round_trips_after_hard_restarts_and_patch(start_app):
    process = start_app()
    with make_app_client(process.url) as client:
        ordinary = create_trip(client, budgetUsd="420.10")
        zero = create_trip(client, budgetUsd="0.00")

    process.kill()
    process.start()
    with make_app_client(process.url) as client:
        assert client.get(f"/trips/{ordinary['id']}").json()["budgetUsd"] == "420.10"
        assert client.get(f"/trips/{zero['id']}").json()["budgetUsd"] == "0.00"
        patched = client.patch(
            f"/trips/{ordinary['id']}", json={"version": ordinary["version"], "budgetUsd": "0.00"}
        )
        assert patched.status_code == 200
        assert patched.json()["budgetUsd"] == "0.00"

    process.kill()
    process.start()
    with make_app_client(process.url) as client:
        assert client.get(f"/trips/{ordinary['id']}").json()["budgetUsd"] == "0.00"


def test_trip_under_way_remains_editable_unless_start_date_changes(app_client, db_engine):
    today = datetime.now(timezone.utc).date()
    trip_id = uuid4()
    starts_on = today - timedelta(days=5)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO trips (id, starts_on, ends_on, budget_usd, travelers, version) "
                "VALUES (:id, :starts, :ends, 10.00, 2, 1)"
            ),
            {"id": trip_id, "starts": starts_on, "ends": today + timedelta(days=5)},
        )
        connection.execute(
            text("INSERT INTO trip_destinations (trip_id, position, name) VALUES (:id, 0, 'Lima')"),
            {"id": trip_id},
        )
        connection.execute(
            text(
                "INSERT INTO trip_participants (trip_id, user_id, role) "
                "VALUES (:id, 'ana', 'organizer')"
            ),
            {"id": trip_id},
        )

    changes = [
        {"budgetUsd": "11.00"},
        {"startsOn": starts_on.isoformat()},
        {"endsOn": (today + timedelta(days=6)).isoformat()},
        {"destinations": ["Lima", "Cusco"]},
    ]
    version = 1
    for change in changes:
        response = app_client.patch(f"/trips/{trip_id}", json={"version": version} | change)
        assert response.status_code == 200, response.text
        version += 1
        assert response.json()["version"] == version

    invalid_start = today - timedelta(days=2)
    refused = app_client.patch(
        f"/trips/{trip_id}", json={"version": version, "startsOn": invalid_start.isoformat()}
    )
    assert_validation_error(refused, "startsOn")
    current = app_client.get(f"/trips/{trip_id}").json()
    assert current["version"] == version
    assert current["startsOn"] == starts_on.isoformat()
