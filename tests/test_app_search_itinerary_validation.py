"""Iteration 3 requirement 14: search and itinerary validation stays actionable."""
from uuid import uuid4

import pytest

from app_support import (
    AppProcess,
    ProviderStub,
    StubResponse,
    app_client as make_app_client,
    assert_validation_error,
    create_planning_trip,
    raw_json_request,
    valid_trip_body,
)


def search(client, trip_id, body):
    return client.post(f"/trips/{trip_id}/search", json=body)


@pytest.fixture(scope="module")
def validation_provider_app(postgres):
    with ProviderStub(lambda _request: StubResponse(500)) as stub:
        process = AppProcess(
            postgres.database_url,
            PROVIDER_BASE_URL=stub.url,
            PROVIDER_TOKEN="token-a",
        )
        process.start()
        try:
            yield process, stub
        finally:
            process.kill()


@pytest.mark.parametrize(
    "body,field",
    [
        ({}, "category"),
        ({"category": "lodging"}, "category"),
        ({"category": "transport", "destinations": ["Lisbon"]}, "origin"),
        (
            {
                "category": "accommodation",
                "startsOn": "2020-01-01",
                "endsOn": "2020-01-02",
            },
            "startsOn",
        ),
        (
            {
                "category": "accommodation",
                "startsOn": "2030-06-13",
                "endsOn": "2030-06-10",
            },
            "body",
        ),
        (
            {"category": "accommodation", "destinations": [f"City {index}" for index in range(21)]},
            "destinations",
        ),
        ({"category": "accommodation", "destinations": []}, "destinations"),
        ({"category": "accommodation", "providerId": ""}, "providerId"),
        ({"category": "accommodation", "providerId": "x" * 129}, "providerId"),
        ({"category": "accommodation", "unexpected": True}, "body"),
        ({"category": "accommodation", "travelers": 20}, "body"),
    ],
    ids=[
        "missing-category",
        "unknown-category",
        "transport-origin",
        "past-start",
        "reversed-dates",
        "too-many-destinations",
        "empty-destinations",
        "empty-provider-id",
        "long-provider-id",
        "unknown-field",
        "client-travelers",
    ],
)
def test_invalid_search_fields_are_422_and_never_reach_the_provider(
    validation_provider_app, control, body, field
):
    process, stub = validation_provider_app
    stub.responder = lambda _request: StubResponse(500)

    with make_app_client(process.url) as client:
        trip = create_planning_trip(client)
        stub.requests.clear()
        response = search(client, trip["id"], body)
        assert_validation_error(response, field)
        assert stub.requests == []


@pytest.mark.parametrize(
    "body,field",
    [
        ({"category": "accommodation", "destinations": ["Lis\x00bon"]}, "destinations[0]"),
        (
            {
                "category": "transport",
                "origin": "Lon\x00don",
                "destinations": ["Lisbon"],
            },
            "origin",
        ),
        ({"category": "accommodation", "providerId": "supplier\x00a"}, "providerId"),
    ],
    ids=["destination", "transport-origin", "provider-id"],
)
def test_search_refuses_nul_before_calling_the_provider(
    validation_provider_app, control, body, field
):
    process, stub = validation_provider_app
    stub.responder = lambda _request: StubResponse(500)

    with make_app_client(process.url) as client:
        trip = create_planning_trip(client)
        stub.requests.clear()
        response = raw_json_request(
            client, "POST", f"/trips/{trip['id']}/search", body
        )
        assert_validation_error(response, field)
        assert stub.requests == []


@pytest.mark.parametrize("offer_id", ["", "x" * 129, None], ids=["empty", "too-long", "null"])
def test_bad_offer_id_is_422_and_saves_nothing(app_client, control, offer_id):
    trip = create_planning_trip(app_client)
    response = app_client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={"version": 1, "offerId": offer_id},
    )
    assert_validation_error(response, "offerId")
    view = app_client.get(f"/trips/{trip['id']}/itinerary").json()
    assert view["version"] == 1
    assert view["items"] == []


@pytest.mark.parametrize("version", ["1", 1.0, True], ids=["string", "float", "boolean"])
def test_itinerary_version_is_a_strict_integer(app_client, control, version):
    trip = create_planning_trip(app_client)
    response = app_client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={"version": version, "offerId": "offer-hotel-001"},
    )
    assert_validation_error(response, "version")


@pytest.mark.parametrize("operation", ["order", "refresh"])
@pytest.mark.parametrize("version", ["1", 1.0, True], ids=["string", "float", "boolean"])
def test_order_and_refresh_versions_are_strict_integers(
    app_client, control, operation, version
):
    trip = create_planning_trip(app_client)
    added = app_client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={"version": 1, "offerId": "offer-hotel-001"},
    ).json()
    item = added["items"][0]
    if operation == "order":
        response = app_client.put(
            f"/trips/{trip['id']}/itinerary/order",
            json={"version": version, "itemIds": [item["id"]]},
        )
    else:
        response = app_client.post(
            f"/trips/{trip['id']}/itinerary/items/{item['id']}/refresh",
            json={"version": version},
        )
    assert_validation_error(response, "version")
    assert app_client.get(f"/trips/{trip['id']}/itinerary").json() == added


@pytest.mark.parametrize("kind", ["missing", "duplicate", "extra"])
def test_order_requires_an_exact_permutation(app_client, control, kind):
    trip = create_planning_trip(app_client)
    first = app_client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={"version": 1, "offerId": "offer-hotel-001"},
    ).json()
    second = app_client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={"version": 2, "offerId": "offer-activity-001"},
    ).json()
    item_ids = [item["id"] for item in second["items"]]
    if kind == "missing":
        sent = item_ids[:1]
    elif kind == "duplicate":
        sent = [item_ids[0], item_ids[0]]
    else:
        sent = item_ids + [str(uuid4())]
    response = app_client.put(
        f"/trips/{trip['id']}/itinerary/order",
        json={"version": 3, "itemIds": sent},
    )
    assert_validation_error(response, "itemIds")
    assert app_client.get(f"/trips/{trip['id']}/itinerary").json() == second


@pytest.mark.parametrize("travelers", [0, 21, "2", 2.0, True, None])
def test_trip_travelers_are_required_strict_and_bounded(
    app_client, database_counts, travelers
):
    response = app_client.post("/trips", json=valid_trip_body(travelers=travelers))
    assert_validation_error(response, "travelers")
    assert database_counts() == (0, 0, 0)


def test_trip_travelers_is_required(app_client, database_counts):
    body = valid_trip_body()
    del body["travelers"]
    response = app_client.post("/trips", json=body)
    assert_validation_error(response, "travelers")
    assert database_counts() == (0, 0, 0)


@pytest.mark.parametrize("travelers", [0, 21, "2", 2.0, True, None])
def test_patch_travelers_is_strict_and_bounded(app_client, travelers):
    trip = create_planning_trip(app_client)
    response = app_client.patch(
        f"/trips/{trip['id']}", json={"version": 1, "travelers": travelers}
    )
    assert_validation_error(response, "travelers")
    assert app_client.get(f"/trips/{trip['id']}").json() == trip


def test_trip_travelers_round_trip_and_can_be_patched(app_client):
    trip = create_planning_trip(app_client)
    assert trip["travelers"] == 2
    changed = app_client.patch(
        f"/trips/{trip['id']}", json={"version": 1, "travelers": 20}
    )
    assert changed.status_code == 200
    assert changed.json()["travelers"] == 20
    assert changed.json()["version"] == 2


def test_stale_order_wins_over_non_permutation(app_client, control):
    trip = create_planning_trip(app_client)
    added = app_client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={"version": 1, "offerId": "offer-hotel-001"},
    ).json()
    response = app_client.put(
        f"/trips/{trip['id']}/itinerary/order",
        json={"version": 1, "itemIds": []},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert response.json()["details"]["current"] == added


@pytest.mark.parametrize("operation", ["add", "order", "refresh"])
def test_huge_body_version_is_a_conflict_not_a_database_error(
    app_client, control, operation
):
    trip = create_planning_trip(app_client)
    added = app_client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={"version": 1, "offerId": "offer-hotel-001"},
    ).json()
    item = added["items"][0]
    if operation == "add":
        response = app_client.post(
            f"/trips/{trip['id']}/itinerary/items",
            json={"version": 10**100, "offerId": "must-not-be-requested"},
        )
    elif operation == "order":
        response = app_client.put(
            f"/trips/{trip['id']}/itinerary/order",
            json={"version": 10**100, "itemIds": [item["id"]]},
        )
    else:
        response = app_client.post(
            f"/trips/{trip['id']}/itinerary/items/{item['id']}/refresh",
            json={"version": 10**100},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "VERSION_CONFLICT"
    assert body["details"]["current"] == app_client.get(
        f"/trips/{trip['id']}/itinerary"
    ).json()


def test_huge_delete_query_version_is_a_conflict(app_client, control):
    trip = create_planning_trip(app_client)
    added = app_client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={"version": 1, "offerId": "offer-hotel-001"},
    ).json()
    item = added["items"][0]
    response = app_client.delete(
        f"/trips/{trip['id']}/itinerary/items/{item['id']}?version={'1' * 41}"
    )
    body = response.json()
    assert response.status_code == 409
    assert body["code"] == "VERSION_CONFLICT"
    assert body["details"]["current"] == app_client.get(
        f"/trips/{trip['id']}/itinerary"
    ).json()


@pytest.mark.parametrize("query", ["version=abc", "version=1.5", ""], ids=["text", "float", "missing"])
def test_delete_query_version_is_required_and_integer(app_client, control, query):
    trip = create_planning_trip(app_client)
    added = app_client.post(
        f"/trips/{trip['id']}/itinerary/items",
        json={"version": 1, "offerId": "offer-hotel-001"},
    ).json()
    item = added["items"][0]
    suffix = f"?{query}" if query else ""
    response = app_client.delete(
        f"/trips/{trip['id']}/itinerary/items/{item['id']}{suffix}"
    )
    assert_validation_error(response, "version")
    assert app_client.get(f"/trips/{trip['id']}/itinerary").json() == added
