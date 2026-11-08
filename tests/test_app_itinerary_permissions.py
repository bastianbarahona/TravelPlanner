"""Iteration 3 requirement 8: role matrix and hidden itinerary records."""
from contextlib import nullcontext

import pytest

from app_support import (
    ProviderStub,
    StubResponse,
    add_participant,
    app_client as make_app_client,
    assert_error,
    create_planning_trip,
    provider_error,
    stub_offer,
    stub_search_result,
)


def add(client, trip_id, version=1, offer_id="offer-hotel-001"):
    return client.post(
        f"/trips/{trip_id}/itinerary/items",
        json={"version": version, "offerId": offer_id},
    )


@pytest.mark.parametrize("role", ["organizer", "editor"])
@pytest.mark.parametrize("operation", ["add", "delete", "order", "refresh"])
def test_organizer_and_editor_can_use_every_itinerary_write(
    app_client, application, control, role, operation
):
    trip = create_planning_trip(app_client)
    if role == "editor":
        add_participant(app_client, trip["id"], "ben", "editor")
        actor_context = make_app_client(application.url, "ben")
    else:
        actor_context = nullcontext(app_client)

    item = None
    version = 1
    if operation != "add":
        first = add(app_client, trip["id"], version).json()
        item = first["items"][0]
        version = first["version"]
    if operation == "order":
        second = add(app_client, trip["id"], version, "offer-activity-001").json()
        version = second["version"]
        item_ids = [item["id"] for item in reversed(second["items"])]
    if operation == "refresh":
        control.change_offer("offer-hotel-001", title=f"Changed for {role}")

    with actor_context as actor:
        if operation == "add":
            response = add(actor, trip["id"], 1)
            assert response.status_code == 201
        elif operation == "delete":
            response = actor.delete(
                f"/trips/{trip['id']}/itinerary/items/{item['id']}?version={version}"
            )
            assert response.status_code == 200
            assert response.json()["items"] == []
        elif operation == "order":
            response = actor.put(
                f"/trips/{trip['id']}/itinerary/order",
                json={"version": version, "itemIds": item_ids},
            )
            assert response.status_code == 200
            assert [entry["id"] for entry in response.json()["items"]] == item_ids
        else:
            response = actor.post(
                f"/trips/{trip['id']}/itinerary/items/{item['id']}/refresh",
                json={"version": version},
            )
            assert response.status_code == 200
            assert response.json()["items"][0]["offer"]["title"] == f"Changed for {role}"
    assert response.json()["version"] == version + 1


@pytest.mark.parametrize("operation", ["add", "delete", "order", "refresh"])
def test_viewer_is_forbidden_before_validation_and_outsider_sees_404_without_provider_call(
    start_app, control, operation
):
    def respond(request):
        if request["method"] == "POST":
            return StubResponse(200, stub_search_result())
        offer_id = request["path"].rsplit("/", 1)[1]
        return StubResponse(200, stub_offer(offer_id))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as organizer:
            trip = create_planning_trip(organizer)
            add_participant(organizer, trip["id"], "ben", "viewer")
            item = None
            version = 1
            if operation != "add":
                view = add(organizer, trip["id"], 1, "setup-offer").json()
                item = view["items"][0]
                version = view["version"]
            stub.requests.clear()

            def call(client):
                if operation == "add":
                    return client.post(
                        f"/trips/{trip['id']}/itinerary/items",
                        json={"version": "invalid", "offerId": ""},
                    )
                if operation == "delete":
                    return client.delete(
                        f"/trips/{trip['id']}/itinerary/items/{item['id']}?version=invalid"
                    )
                if operation == "order":
                    return client.put(
                        f"/trips/{trip['id']}/itinerary/order",
                        json={"version": "invalid", "itemIds": ["not-an-id"]},
                    )
                return client.post(
                    f"/trips/{trip['id']}/itinerary/items/{item['id']}/refresh",
                    json={"version": "invalid"},
                )

            with make_app_client(process.url, "ben") as viewer:
                assert_error(call(viewer), 403, "FORBIDDEN")
            with make_app_client(process.url, "cam") as outsider:
                assert_error(call(outsider), 404, "NOT_FOUND")
            assert stub.requests == []
            assert organizer.get(f"/trips/{trip['id']}/itinerary").json()["version"] == version


@pytest.mark.parametrize("role", ["organizer", "editor", "viewer"])
def test_every_participant_can_read_and_search(
    app_client, application, control, role
):
    trip = create_planning_trip(app_client)
    if role != "organizer":
        add_participant(app_client, trip["id"], "ben", role)
        context = make_app_client(application.url, "ben")
    else:
        context = nullcontext(app_client)
    with context as actor:
        itinerary = actor.get(f"/trips/{trip['id']}/itinerary")
        assert itinerary.status_code == 200
        assert itinerary.json()["tripId"] == trip["id"]
        result = actor.post(
            f"/trips/{trip['id']}/search",
            json={"category": "accommodation"},
        )
        assert result.status_code == 200
        assert result.json()["legs"][0]["status"] == "complete"


@pytest.mark.parametrize("operation", ["delete", "refresh"])
def test_missing_malformed_and_other_trip_items_are_the_same_404(
    app_client, control, operation
):
    first_trip = create_planning_trip(app_client)
    second_trip = create_planning_trip(app_client)
    first_item = add(app_client, first_trip["id"], 1).json()["items"][0]

    def call(trip_id, item_id, version):
        path = f"/trips/{trip_id}/itinerary/items/{item_id}"
        if operation == "delete":
            return app_client.delete(f"{path}?version={version}")
        return app_client.post(f"{path}/refresh", json={"version": version})

    bodies = []
    for item_id in ("00000000-0000-0000-0000-000000000000", "not-a-uuid", first_item["id"]):
        trip = second_trip if item_id == first_item["id"] else first_trip
        current_version = 1 if trip is second_trip else 2
        error = call(trip["id"], item_id, current_version)
        body = assert_error(error, 404, "NOT_FOUND")
        bodies.append({key: value for key, value in body.items() if key != "requestId"})
    assert bodies[0] == bodies[1] == bodies[2]


def test_provider_404_on_add_and_refresh_is_offer_gone_without_changes(start_app, control):
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return StubResponse(200, stub_offer(request["path"].rsplit("/", 1)[1]))
        return StubResponse(404, provider_error("NOT_FOUND"))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            created = add(client, trip["id"], 1, "later-gone")
            # The first response deliberately creates the item; the second proves refresh is atomic.
            assert created.status_code == 201
            before = created.json()
            item = before["items"][0]
            gone = client.post(
                f"/trips/{trip['id']}/itinerary/items/{item['id']}/refresh",
                json={"version": 2},
            )
            assert_error(gone, 409, "OFFER_GONE")
            assert client.get(f"/trips/{trip['id']}/itinerary").json() == before

            add_gone = add(client, trip["id"], 2, "never-there")
            assert_error(add_gone, 409, "OFFER_GONE")
            assert client.get(f"/trips/{trip['id']}/itinerary").json() == before
