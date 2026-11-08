"""Iteration 3 requirements 11-12: forced edit races and released DB connections."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app_support import (
    ProviderStub,
    StubResponse,
    app_client as make_app_client,
    assert_error,
    create_planning_trip,
    install_delay_trigger,
    remove_delay_trigger,
    stub_offer,
    stub_search_result,
)
from support import run_concurrently


def add(client, trip_id, version, offer_id="offer-hotel-001"):
    return client.post(
        f"/trips/{trip_id}/itinerary/items",
        json={"version": version, "offerId": offer_id},
    )


def assert_one_winner(responses, client, trip_id, expected_version):
    successes = [response for response in responses if response.status_code in {200, 201}]
    conflicts = [response for response in responses if response.status_code == 409]
    assert len(successes) == 1
    assert len(conflicts) == len(responses) - 1
    assert all(response.json()["code"] == "VERSION_CONFLICT" for response in conflicts)
    assert all(response.status_code != 500 for response in responses)
    final = client.get(f"/trips/{trip_id}/itinerary").json()
    assert final == successes[0].json()
    assert final["version"] == expected_version
    assert all(response.json()["details"]["current"] == final for response in conflicts)
    return final


def test_24_concurrent_adds_have_one_winner_and_one_new_item(
    app_client, control, db_engine
):
    trip = create_planning_trip(app_client)
    install_delay_trigger(db_engine, "test_delay_itinerary_add", "UPDATE", "trips")
    try:
        responses = run_concurrently(
            [lambda: add(app_client, trip["id"], 1) for _ in range(24)]
        )
    finally:
        remove_delay_trigger(db_engine, "test_delay_itinerary_add", "trips")
    final = assert_one_winner(responses, app_client, trip["id"], 2)
    assert len(final["items"]) == 1
    assert final["items"][0]["position"] == 0


def test_24_concurrent_reorders_have_one_winner(app_client, control, db_engine):
    trip = create_planning_trip(app_client)
    view = add(app_client, trip["id"], 1, "offer-hotel-001").json()
    view = add(app_client, trip["id"], 2, "offer-activity-001").json()
    item_ids = [item["id"] for item in reversed(view["items"])]
    install_delay_trigger(db_engine, "test_delay_itinerary_order", "UPDATE", "trips")
    try:
        responses = run_concurrently(
            [
                lambda: app_client.put(
                    f"/trips/{trip['id']}/itinerary/order",
                    json={"version": 3, "itemIds": item_ids},
                )
                for _ in range(24)
            ]
        )
    finally:
        remove_delay_trigger(db_engine, "test_delay_itinerary_order", "trips")
    final = assert_one_winner(responses, app_client, trip["id"], 4)
    assert [item["id"] for item in final["items"]] == item_ids


def test_24_mixed_writers_commit_exactly_one_complete_state(
    app_client, control, db_engine
):
    trip = create_planning_trip(app_client)
    view = add(app_client, trip["id"], 1, "offer-hotel-001").json()
    view = add(app_client, trip["id"], 2, "offer-activity-001").json()
    first_id = view["items"][0]["id"]
    reversed_ids = [item["id"] for item in reversed(view["items"])]
    control.change_offer("offer-hotel-001", title="Fresh during mixed writers")
    calls = []
    for _ in range(6):
        calls.append(lambda: add(app_client, trip["id"], 3, "offer-hotel-002"))
        calls.append(
            lambda: app_client.put(
                f"/trips/{trip['id']}/itinerary/order",
                json={"version": 3, "itemIds": reversed_ids},
            )
        )
        calls.append(
            lambda: app_client.delete(
                f"/trips/{trip['id']}/itinerary/items/{first_id}?version=3"
            )
        )
        calls.append(
            lambda: app_client.post(
                f"/trips/{trip['id']}/itinerary/items/{first_id}/refresh",
                json={"version": 3},
            )
        )
    install_delay_trigger(db_engine, "test_delay_itinerary_mixed", "UPDATE", "trips")
    try:
        responses = run_concurrently(calls)
    finally:
        remove_delay_trigger(db_engine, "test_delay_itinerary_mixed", "trips")
    final = assert_one_winner(responses, app_client, trip["id"], 4)
    assert len(final["items"]) in {1, 2, 3}
    assert [item["position"] for item in final["items"]] == list(range(len(final["items"])))


@pytest.mark.parametrize("operation", ["add", "delete", "order", "refresh"])
def test_trip_patch_makes_every_itinerary_write_stale(
    start_app, control, operation
):
    with ProviderStub(lambda request: StubResponse(200, stub_offer(request["path"].rsplit("/", 1)[1]))) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            if operation == "add":
                version = 1
                items = []
            else:
                view = add(client, trip["id"], 1, "first-offer").json()
                view = add(client, trip["id"], 2, "second-offer").json()
                version = view["version"]
                items = view["items"]
            patched = client.patch(
                f"/trips/{trip['id']}", json={"version": version, "budgetUsd": "900.00"}
            )
            assert patched.status_code == 200
            stub.requests.clear()
            if operation == "add":
                response = add(client, trip["id"], version, "must-not-be-requested")
            elif operation == "delete":
                response = client.delete(
                    f"/trips/{trip['id']}/itinerary/items/{items[0]['id']}?version={version}"
                )
            elif operation == "order":
                response = client.put(
                    f"/trips/{trip['id']}/itinerary/order",
                    json={
                        "version": version,
                        "itemIds": [item["id"] for item in reversed(items)],
                    },
                )
            else:
                response = client.post(
                    f"/trips/{trip['id']}/itinerary/items/{items[0]['id']}/refresh",
                    json={"version": version},
                )
            error = assert_error(response, 409, "VERSION_CONFLICT")
            assert error["details"]["current"] == client.get(
                f"/trips/{trip['id']}/itinerary"
            ).json()
            assert stub.requests == []


def test_itinerary_write_makes_trip_patch_stale(start_app, control):
    with ProviderStub(lambda request: StubResponse(200, stub_offer(request["path"].rsplit("/", 1)[1]))) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as client:
            second = create_planning_trip(client)
            added = add(client, second["id"], 1, "stub-offer")
            assert added.status_code == 201
            stale_patch = client.patch(
                f"/trips/{second['id']}", json={"version": 1, "budgetUsd": "900.00"}
            )
            patch_error = assert_error(stale_patch, 409, "VERSION_CONFLICT")
            assert patch_error["details"]["current"] == client.get(f"/trips/{second['id']}").json()


@pytest.mark.parametrize("operation", ["delete", "reorder"])
def test_item_change_while_write_waits_is_a_version_conflict(
    app_client, control, db_engine, operation
):
    trip = create_planning_trip(app_client)
    view = add(app_client, trip["id"], 1, "offer-hotel-001").json()
    view = add(app_client, trip["id"], 2, "offer-activity-001").json()
    items = view["items"]
    connection = db_engine.connect()
    pool = ThreadPoolExecutor(1)
    try:
        connection.execute(text("LOCK TABLE itinerary_items IN ACCESS EXCLUSIVE MODE"))
        if operation == "delete":
            future = pool.submit(
                app_client.delete,
                f"/trips/{trip['id']}/itinerary/items/{items[0]['id']}?version=3",
            )
        else:
            future = pool.submit(
                app_client.put,
                f"/trips/{trip['id']}/itinerary/order",
                json={"version": 3, "itemIds": [item["id"] for item in reversed(items)]},
            )

        deadline = time.monotonic() + 5
        while True:
            with db_engine.connect() as observer:
                waiting = observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                        "AND query ILIKE '%itinerary_items%'"
                    )
                )
            if waiting:
                break
            assert time.monotonic() < deadline, "request never waited on the itinerary_items lock"
            time.sleep(0.02)

        if operation == "delete":
            connection.execute(
                text("DELETE FROM itinerary_items WHERE id = :id"),
                {"id": items[0]["id"]},
            )
            connection.execute(
                text(
                    "UPDATE itinerary_items SET position = position - 1 "
                    "WHERE trip_id = :trip_id AND position > 0"
                ),
                {"trip_id": trip["id"]},
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO itinerary_items (id, trip_id, position, offer) "
                    "VALUES (:id, :trip_id, 2, CAST(:offer AS jsonb))"
                ),
                {
                    "id": uuid4(),
                    "trip_id": trip["id"],
                    "offer": json.dumps(stub_offer("race-added-offer")),
                },
            )
        connection.execute(
            text("UPDATE trips SET version = version + 1 WHERE id = :trip_id"),
            {"trip_id": trip["id"]},
        )
        connection.commit()
        response = future.result(timeout=5)
        error = assert_error(response, 409, "VERSION_CONFLICT")
        current = app_client.get(f"/trips/{trip['id']}/itinerary").json()
        assert current["version"] == 4
        assert error["details"]["current"] == current
    finally:
        connection.rollback()
        connection.close()
        pool.shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize("workflow", ["search", "add", "refresh"])
def test_provider_wait_holds_no_database_transaction(start_app, control, db_engine, workflow):
    entered = threading.Event()
    release = threading.Event()
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        if workflow == "refresh" and attempts == 1:
            return StubResponse(200, stub_offer("held-offer"))
        entered.set()
        assert release.wait(5)
        if request["method"] == "POST":
            return StubResponse(200, stub_search_result())
        return StubResponse(200, stub_offer(request["path"].rsplit("/", 1)[1]))

    with ProviderStub(respond) as stub:
        # The document worker's and the booking poller's own claims would show up in pg_stat_activity; this test
        # is about the request path.
        process = start_app(PROVIDER_BASE_URL=stub.url, DOCUMENT_WORKER_ENABLED="0", BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client, ThreadPoolExecutor(1) as pool:
            trip = create_planning_trip(client)
            if workflow == "search":
                future = pool.submit(
                    client.post,
                    f"/trips/{trip['id']}/search",
                    json={"category": "accommodation"},
                )
            elif workflow == "add":
                future = pool.submit(add, client, trip["id"], 1, "held-offer")
            else:
                seeded = add(client, trip["id"], 1, "held-offer").json()
                future = pool.submit(
                    client.post,
                    f"/trips/{trip['id']}/itinerary/items/{seeded['items'][0]['id']}/refresh",
                    json={"version": seeded["version"]},
                )
            try:
                assert entered.wait(3)
                with db_engine.connect() as connection:
                    states = connection.execute(
                        text(
                            "SELECT state FROM pg_stat_activity "
                            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                        )
                    ).scalars().all()
                assert "idle in transaction" not in states
            finally:
                release.set()
            response = future.result(timeout=5)
            assert response.status_code in {200, 201}


def test_health_answers_within_one_second_while_50_searches_wait(start_app, control):
    release = threading.Event()

    def respond(_request):
        assert release.wait(15)
        return StubResponse(200, stub_search_result())

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, PROVIDER_TIMEOUT_SECONDS="20")
        with make_app_client(process.url) as client, ThreadPoolExecutor(50) as pool:
            trip = create_planning_trip(client)
            barrier = Barrier(50)

            def call():
                barrier.wait()
                return client.post(
                    f"/trips/{trip['id']}/search",
                    json={"category": "accommodation"},
                )

            futures = [pool.submit(call) for _ in range(50)]
            try:
                assert stub.wait_for_requests(50, timeout=10)
                started = time.monotonic()
                health = httpx.get(
                    f"{process.url}/health", timeout=1, verify=False, trust_env=False
                )
                elapsed = time.monotonic() - started
                assert health.status_code == 200
                assert health.json() == {"status": "ok"}
                assert elapsed < 1
            finally:
                release.set()
            assert all(future.result(timeout=15).status_code == 200 for future in futures)
