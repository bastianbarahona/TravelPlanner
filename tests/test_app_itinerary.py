"""Iteration 3 requirements 5, 7, 9 and 10: itinerary, issues and budget."""
import importlib.util
import re

from app_support import (
    APP_SOURCE_ROOT,
    ProviderStub,
    StubResponse,
    app_client as make_app_client,
    assert_error,
    assert_validation_error,
    create_planning_trip,
    stub_offer,
)
from support import current_offer


def add(client, trip_id, version, offer_id):
    return client.post(
        f"/trips/{trip_id}/itinerary/items",
        json={"version": version, "offerId": offer_id},
    )


def refresh(client, trip_id, item_id, version):
    return client.post(
        f"/trips/{trip_id}/itinerary/items/{item_id}/refresh",
        json={"version": version},
    )


def issue(view, code):
    return [entry for entry in view["issues"] if entry["code"] == code]


def test_add_reorder_swap_remove_and_each_state_survives_a_hard_kill(start_app, control):
    process = start_app()
    with make_app_client(process.url) as client:
        trip = create_planning_trip(client)
        first = add(client, trip["id"], 1, "offer-hotel-001")
        assert first.status_code == 201
        second = add(client, trip["id"], 2, "offer-activity-001")
        assert second.status_code == 201
        added = second.json()
        assert added["version"] == 3
        assert [item["position"] for item in added["items"]] == [0, 1]

    process.kill()
    process.start()
    with make_app_client(process.url) as client:
        persisted = client.get(f"/trips/{trip['id']}/itinerary").json()
        assert persisted == added
        reversed_ids = [item["id"] for item in reversed(persisted["items"])]
        reordered_response = client.put(
            f"/trips/{trip['id']}/itinerary/order",
            json={"version": 3, "itemIds": reversed_ids},
        )
        assert reordered_response.status_code == 200
        reordered = reordered_response.json()
        assert reordered["version"] == 4
        assert [item["id"] for item in reordered["items"]] == reversed_ids
        assert [item["position"] for item in reordered["items"]] == [0, 1]

    process.kill()
    process.start()
    with make_app_client(process.url) as client:
        assert client.get(f"/trips/{trip['id']}/itinerary").json() == reordered
        removed_id = reordered["items"][0]["id"]
        removed_response = client.delete(
            f"/trips/{trip['id']}/itinerary/items/{removed_id}?version=4"
        )
        assert removed_response.status_code == 200
        removed = removed_response.json()
        assert removed["version"] == 5
        assert [item["id"] for item in removed["items"]] == [reordered["items"][1]["id"]]
        assert removed["items"][0]["position"] == 0

    process.kill()
    process.start()
    with make_app_client(process.url) as client:
        assert client.get(f"/trips/{trip['id']}/itinerary").json() == removed


def test_add_refuses_client_price_and_stores_provider_json_without_reencoding(
    app_client, provider, control
):
    trip = create_planning_trip(app_client)
    refused = app_client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={
            "version": 1,
            "offerId": "offer-hotel-001",
            "price": {"amount": "0.01", "currency": "USD"},
        },
    )
    assert_validation_error(refused, "body")

    expected_free = current_offer(provider, "offer-activity-001")
    expected_without_restrictions = current_offer(provider, "offer-hotel-003")
    first = add(app_client, trip["id"], 1, expected_free["id"])
    assert first.status_code == 201
    second = add(app_client, trip["id"], 2, expected_without_restrictions["id"])
    assert second.status_code == 201
    snapshots = {item["offer"]["id"]: item["offer"] for item in second.json()["items"]}
    assert snapshots[expected_free["id"]] == expected_free
    assert snapshots[expected_without_restrictions["id"]] == expected_without_restrictions
    assert snapshots[expected_free["id"]]["price"]["amount"] == "0.00"
    assert "+01:00" in snapshots[expected_free["id"]]["startsAt"]
    assert "restrictions" not in snapshots[expected_without_restrictions["id"]]


def test_unavailable_add_expiry_refresh_and_unchanged_refresh(app_client, control):
    trip = create_planning_trip(app_client)
    searched = app_client.post(
        f"/trips/{trip['id']}/search", json={"category": "accommodation"}
    ).json()
    unavailable_option = next(
        option
        for option in searched["legs"][0]["options"]
        if option["offer"]["id"] == "offer-hotel-005"
    )
    assert unavailable_option["expired"] is False
    assert unavailable_option["bookable"] is False

    unavailable = add(app_client, trip["id"], 1, "offer-hotel-005")
    assert_error(unavailable, 409, "OFFER_UNAVAILABLE")
    assert app_client.get(f"/trips/{trip['id']}/itinerary").json()["version"] == 1

    control.pin_clock("2019-01-01T00:00:00Z")
    control.change_offer("offer-hotel-001", validUntil="2020-01-01T00:00:00Z")
    expired_response = add(app_client, trip["id"], 1, "offer-hotel-001")
    assert expired_response.status_code == 201
    expired = expired_response.json()
    item = expired["items"][0]
    assert item["expired"] is True
    assert item["bookable"] is False
    assert issue(expired, "PRICE_EXPIRED")[0]["itemIds"] == [item["id"]]

    control.change_offer("offer-hotel-001", validUntil="2099-01-01T00:00:00Z")
    refreshed_response = refresh(app_client, trip["id"], item["id"], 2)
    assert refreshed_response.status_code == 200
    refreshed = refreshed_response.json()
    assert refreshed["version"] == 3
    assert refreshed["items"][0]["expired"] is False
    assert refreshed["items"][0]["bookable"] is True
    assert not issue(refreshed, "PRICE_EXPIRED")

    unchanged = refresh(app_client, trip["id"], item["id"], 3)
    assert unchanged.status_code == 200
    assert unchanged.json() == refreshed


def test_destination_without_stay_uses_casefold_and_clears_when_a_stay_is_added(
    app_client, control
):
    trip = create_planning_trip(app_client, destinations=["LISBON", "Porto"])
    one_stay = add(app_client, trip["id"], 1, "offer-hotel-001").json()
    assert not issue(one_stay, "MISSING_ORIGIN")
    missing = issue(one_stay, "DESTINATION_WITHOUT_STAY")
    assert missing == [
        {
            "code": "DESTINATION_WITHOUT_STAY",
            "message": missing[0]["message"],
            "itemIds": [],
            "destination": "Porto",
        }
    ]

    complete = add(app_client, trip["id"], 2, "offer-porto-hotel-001").json()
    assert not issue(complete, "DESTINATION_WITHOUT_STAY")
    assert complete["rules"] and all(isinstance(rule, str) and rule for rule in complete["rules"])


def test_party_mismatch_and_outside_dates_name_only_the_bad_items(app_client, control):
    control.change_offer("offer-hotel-001", travelers=1)
    control.change_offer(
        "offer-hotel-003",
        travelers=1,
        startsAt="2030-06-09T15:00:00+01:00",
        endsAt="2030-06-12T11:00:00+01:00",
    )
    control.change_offer(
        "offer-hotel-004",
        travelers=1,
        startsAt="2030-06-11T15:00:00+01:00",
        endsAt="2030-06-14T11:00:00+01:00",
    )
    control.change_offer("offer-activity-003", travelers=1)
    trip = create_planning_trip(app_client, travelers=1)
    normal = add(app_client, trip["id"], 1, "offer-hotel-001").json()["items"][0]
    mismatch_view = add(app_client, trip["id"], 2, "offer-hotel-002").json()
    mismatch = next(item for item in mismatch_view["items"] if item["offer"]["id"] == "offer-hotel-002")
    assert issue(mismatch_view, "PARTY_MISMATCH")[0]["itemIds"] == [mismatch["id"]]
    assert normal["id"] not in issue(mismatch_view, "PARTY_MISMATCH")[0]["itemIds"]

    outside_view = add(app_client, trip["id"], 3, "offer-hotel-003").json()
    outside_view = add(app_client, trip["id"], 4, "offer-hotel-004").json()
    outside_ids = {
        item["offer"]["id"]: item["id"] for item in outside_view["items"]
    }
    outside_issues = issue(outside_view, "OUTSIDE_TRIP_DATES")
    assert [entry["itemIds"] for entry in outside_issues] == [
        [outside_ids["offer-hotel-003"]],
        [outside_ids["offer-hotel-004"]],
    ]
    assert all(normal["id"] not in entry["itemIds"] for entry in outside_issues)

    literal_date_view = add(app_client, trip["id"], 5, "offer-activity-003").json()
    local_midnight = next(
        item for item in literal_date_view["items"] if item["offer"]["id"] == "offer-activity-003"
    )
    assert all(
        local_midnight["id"] not in entry["itemIds"]
        for entry in issue(literal_date_view, "OUTSIDE_TRIP_DATES")
    )
    assert app_client.get(f"/trips/{trip['id']}/itinerary").json()["issues"] == literal_date_view["issues"]


def test_refresh_stores_unavailability_and_reports_only_that_item(app_client, control):
    trip = create_planning_trip(app_client)
    first = add(app_client, trip["id"], 1, "offer-hotel-001").json()
    first_item = first["items"][0]
    normal = add(app_client, trip["id"], 2, "offer-activity-001").json()["items"][1]
    control.change_offer("offer-hotel-001", available=False)

    view = refresh(app_client, trip["id"], first_item["id"], 3).json()
    changed = next(item for item in view["items"] if item["id"] == first_item["id"])
    assert changed["offer"]["available"] is False
    assert changed["bookable"] is False
    assert issue(view, "UNAVAILABLE")[0]["itemIds"] == [first_item["id"]]
    assert normal["id"] not in issue(view, "UNAVAILABLE")[0]["itemIds"]


def test_transport_without_origin_is_reported_but_one_with_origin_is_not(start_app, control):
    offers = {
        "missing-origin": stub_offer(
            "missing-origin",
            category="transport",
            title="Origin omitted",
            startsAt="2030-06-10T09:00:00Z",
            endsAt="2030-06-10T10:00:00Z",
        ),
        "has-origin": stub_offer(
            "has-origin",
            category="transport",
            title="Origin present",
            origin="London",
            startsAt="2030-06-10T11:00:00Z",
            endsAt="2030-06-10T12:00:00Z",
        ),
    }

    def respond(request):
        offer_id = request["path"].rsplit("/", 1)[1]
        return StubResponse(200, offers[offer_id])

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            first = add(client, trip["id"], 1, "missing-origin").json()
            second = add(client, trip["id"], 2, "has-origin").json()
        missing_item = first["items"][0]
        present_item = next(item for item in second["items"] if item["offer"]["id"] == "has-origin")
        assert issue(second, "MISSING_ORIGIN")[0]["itemIds"] == [missing_item["id"]]
        assert present_item["id"] not in issue(second, "MISSING_ORIGIN")[0]["itemIds"]


def test_overlap_uses_instants_not_timestamp_string_order(app_client, control):
    control.change_offer(
        "offer-hotel-001",
        startsAt="2030-06-10T10:00:00+01:00",
        endsAt="2030-06-10T11:00:00+01:00",
    )
    control.change_offer(
        "offer-hotel-002",
        startsAt="2030-06-10T10:30:00Z",
        endsAt="2030-06-10T11:30:00Z",
    )
    control.change_offer(
        "offer-hotel-003",
        startsAt="2030-06-10T10:00:00+01:00",
        endsAt="2030-06-10T10:30:00+01:00",
    )
    control.change_offer(
        "offer-hotel-004",
        startsAt="2030-06-10T09:15:00Z",
        endsAt="2030-06-10T09:45:00Z",
    )
    trip = create_planning_trip(app_client)
    view = None
    for version, offer_id in enumerate(
        ["offer-hotel-001", "offer-hotel-002", "offer-hotel-003", "offer-hotel-004"],
        start=1,
    ):
        view = add(app_client, trip["id"], version, offer_id).json()
    ids = {item["offer"]["id"]: item["id"] for item in view["items"]}
    pairs = [set(entry["itemIds"]) for entry in issue(view, "SCHEDULE_OVERLAP")]
    assert {ids["offer-hotel-001"], ids["offer-hotel-002"]} not in pairs
    assert {ids["offer-hotel-003"], ids["offer-hotel-004"]} in pairs


def test_transport_and_activity_overlap_but_touching_same_categories_do_not(
    app_client, control
):
    control.change_offer(
        "offer-transport-001",
        startsAt="2030-06-10T10:00:00Z",
        endsAt="2030-06-10T12:00:00Z",
    )
    control.change_offer(
        "offer-transport-002",
        startsAt="2030-06-10T11:00:00Z",
        endsAt="2030-06-10T13:00:00Z",
    )
    control.change_offer(
        "offer-hotel-001",
        startsAt="2030-06-10T10:30:00Z",
        endsAt="2030-06-10T11:30:00Z",
    )
    control.change_offer(
        "offer-activity-001",
        startsAt="2030-06-10T14:00:00Z",
        endsAt="2030-06-10T16:00:00Z",
    )
    control.change_offer(
        "offer-activity-002",
        startsAt="2030-06-10T15:00:00Z",
        endsAt="2030-06-10T17:00:00Z",
    )
    control.change_offer(
        "offer-activity-003",
        startsAt="2030-06-10T10:30:00Z",
        endsAt="2030-06-10T10:45:00Z",
    )
    control.change_offer(
        "offer-hotel-002",
        startsAt="2030-06-10T18:00:00Z",
        endsAt="2030-06-10T19:00:00Z",
    )
    control.change_offer(
        "offer-hotel-003",
        startsAt="2030-06-10T19:00:00Z",
        endsAt="2030-06-10T20:00:00Z",
    )
    trip = create_planning_trip(app_client)
    view = None
    for version, offer_id in enumerate(
        [
            "offer-transport-001",
            "offer-transport-002",
            "offer-hotel-001",
            "offer-activity-001",
            "offer-activity-002",
            "offer-activity-003",
            "offer-hotel-002",
            "offer-hotel-003",
        ],
        start=1,
    ):
        view = add(app_client, trip["id"], version, offer_id).json()
    ids = {item["offer"]["id"]: item["id"] for item in view["items"]}
    pairs = [set(entry["itemIds"]) for entry in issue(view, "SCHEDULE_OVERLAP")]
    assert {ids["offer-transport-001"], ids["offer-transport-002"]} in pairs
    assert {ids["offer-activity-001"], ids["offer-activity-002"]} in pairs
    assert {ids["offer-transport-001"], ids["offer-activity-003"]} in pairs
    assert {ids["offer-transport-001"], ids["offer-hotel-001"]} not in pairs
    assert {ids["offer-hotel-001"], ids["offer-activity-003"]} not in pairs
    assert {ids["offer-hotel-002"], ids["offer-hotel-003"]} not in pairs


def test_budget_lines_exact_totals_free_item_and_comparison_are_stable(app_client, control):
    trip = create_planning_trip(app_client, budgetUsd="1000.00")
    view = add(app_client, trip["id"], 1, "offer-hotel-001").json()
    view = add(app_client, trip["id"], 2, "offer-activity-001").json()
    budget = view["budget"]
    assert [line["itemId"] for line in budget["lines"]] == [item["id"] for item in view["items"]]
    assert [line["amountUsd"] for line in budget["lines"]] == ["420.00", "0.00"]
    assert [line["title"] for line in budget["lines"]] == [item["offer"]["title"] for item in view["items"]]
    assert budget["estimatedUsd"] == budget["totalUsd"] == "420.00"
    assert budget["confirmedUsd"] == "0.00"
    assert budget["differenceUsd"] == "580.00"
    assert budget["overBudget"] is False
    assert budget["roundingRule"]
    assert app_client.get(f"/trips/{trip['id']}/itinerary").json() == view

    equal = app_client.patch(
        f"/trips/{trip['id']}", json={"version": 3, "budgetUsd": "420.00"}
    )
    assert equal.status_code == 200
    equal_budget = app_client.get(f"/trips/{trip['id']}/itinerary").json()["budget"]
    assert equal_budget["differenceUsd"] == "0.00"
    assert equal_budget["overBudget"] is False

    changed = app_client.patch(
        f"/trips/{trip['id']}", json={"version": 4, "budgetUsd": "100.00"}
    )
    assert changed.status_code == 200
    over = app_client.get(f"/trips/{trip['id']}/itinerary").json()["budget"]
    assert over["overBudget"] is True
    assert over["differenceUsd"] == "320.00"


def test_format_amount_rounds_half_up_from_the_selected_application_source():
    path = APP_SOURCE_ROOT / "app" / "money.py"
    spec = importlib.util.spec_from_file_location("iteration3_money", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from decimal import Decimal

    assert module.format_amount(Decimal("0.125")) == "0.13"
    assert module.format_amount(Decimal("2.675")) == "2.68"
    assert module.format_amount(Decimal("0.124")) == "0.12"


def test_rounding_primitives_exist_only_in_money_module():
    forbidden = ("quantize", "round(", "ROUND_", "float(")
    offenders = []
    for path in (APP_SOURCE_ROOT / "app").rglob("*.py"):
        if path.name == "money.py":
            continue
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if path.name == "config.py" and token == "float(":
                continue
            if token in source:
                offenders.append(f"{path.relative_to(APP_SOURCE_ROOT)}: {token}")
        if re.search(r":[^}\n]*\.2f", source):
            offenders.append(f"{path.relative_to(APP_SOURCE_ROOT)}: .2f formatting")
    assert offenders == []
