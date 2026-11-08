"""Requirements 7, 8 and 9: atomic writes and optimistic concurrency."""
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from app_support import (
    assert_error,
    create_trip,
    install_delay_trigger,
    remove_delay_trigger,
    valid_trip_body,
)
from support import run_concurrently


def install_participant_failure_trigger(db_engine):
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION test_fail_participant_insert() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'forced participant failure'; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER test_fail_participant_insert BEFORE INSERT ON trip_participants "
                "FOR EACH ROW EXECUTE FUNCTION test_fail_participant_insert()"
            )
        )


def remove_participant_failure_trigger(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text("DROP TRIGGER IF EXISTS test_fail_participant_insert ON trip_participants"))
        connection.execute(text("DROP FUNCTION IF EXISTS test_fail_participant_insert()"))


def test_create_trip_rolls_back_every_row_when_organizer_insert_fails(
    app_client, db_engine, database_counts
):
    body = valid_trip_body()
    install_participant_failure_trigger(db_engine)
    try:
        response = app_client.post("/trips", json=body)
        assert_error(response, 500, "INTERNAL_ERROR")
        assert database_counts() == (0, 0, 0)
    finally:
        remove_participant_failure_trigger(db_engine)
    assert app_client.post("/trips", json=body).status_code == 201


def test_stale_and_huge_versions_return_current_state_without_changes(app_client):
    trip = create_trip(app_client)
    first = app_client.patch(
        f"/trips/{trip['id']}",
        json={"version": 1, "destinations": ["Valparaiso"], "budgetUsd": "100.00"},
    )
    assert first.status_code == 200
    current = first.json()
    assert current["version"] == 2

    stale = app_client.patch(
        f"/trips/{trip['id']}",
        json={"version": 1, "destinations": ["Must not persist"], "budgetUsd": "999.00"},
    )
    stale_error = assert_error(stale, 409, "VERSION_CONFLICT")
    assert stale_error["details"]["current"] == current

    stale_beats_validation = app_client.patch(
        f"/trips/{trip['id']}", json={"version": 1, "endsOn": "2020-01-01"}
    )
    assert_error(stale_beats_validation, 409, "VERSION_CONFLICT")

    huge = app_client.patch(
        f"/trips/{trip['id']}", json={"version": 10**100, "budgetUsd": "1.00"}
    )
    huge_error = assert_error(huge, 409, "VERSION_CONFLICT")
    assert huge_error["details"]["current"] == current
    assert app_client.get(f"/trips/{trip['id']}").json() == current


@pytest.mark.parametrize(
    "route", ["trip", "list"], ids=["GET /trips/{tripId}", "GET /trips"]
)
def test_trip_read_keeps_version_and_destinations_in_one_snapshot(
    app_client, db_engine, route
):
    trip = create_trip(app_client, destinations=["Lisbon"])
    connection = db_engine.connect()
    pool = ThreadPoolExecutor(1)
    try:
        connection.execute(text("LOCK TABLE trip_destinations IN ACCESS EXCLUSIVE MODE"))
        path = f"/trips/{trip['id']}" if route == "trip" else "/trips"
        future = pool.submit(app_client.get, path)

        deadline = time.monotonic() + 5
        while True:
            with db_engine.connect() as observer:
                waiting = observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                        "AND query ILIKE '%trip_destinations%'"
                    )
                )
            if waiting:
                break
            assert time.monotonic() < deadline, f"{path} never waited on the destinations lock"
            time.sleep(0.02)

        connection.execute(
            text(
                "UPDATE trip_destinations SET name = 'Porto' "
                "WHERE trip_id = :trip_id AND position = 0"
            ),
            {"trip_id": trip["id"]},
        )
        connection.execute(
            text("UPDATE trips SET version = version + 1 WHERE id = :trip_id"),
            {"trip_id": trip["id"]},
        )
        connection.commit()

        response = future.result(timeout=5)
        assert response.status_code == 200
        body = response.json()
        if route == "trip":
            view = body
        else:
            [view] = body["trips"]
        assert (view["version"], view["destinations"]) in (
            (1, ["Lisbon"]),
            (2, ["Porto"]),
        )
    finally:
        connection.rollback()
        connection.close()
        pool.shutdown(wait=True, cancel_futures=True)


def test_stale_patch_beats_validation_after_waiting_on_snapshot(app_client, db_engine):
    trip = create_trip(
        app_client,
        startsOn="2030-06-10",
        endsOn="2030-06-13",
    )
    connection = db_engine.connect()
    pool = ThreadPoolExecutor(1)
    try:
        connection.execute(text("LOCK TABLE trip_destinations IN ACCESS EXCLUSIVE MODE"))
        future = pool.submit(
            app_client.patch,
            f"/trips/{trip['id']}",
            json={"version": 1, "endsOn": "2030-06-09"},
        )

        deadline = time.monotonic() + 5
        while True:
            with db_engine.connect() as observer:
                waiting = observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                        "AND query ILIKE '%trip_destinations%'"
                    )
                )
            if waiting:
                break
            assert time.monotonic() < deadline, "PATCH /trips/{tripId} never waited on the destinations lock"
            time.sleep(0.02)

        connection.execute(
            text(
                "UPDATE trips SET starts_on = '2030-06-01', version = version + 1 "
                "WHERE id = :trip_id"
            ),
            {"trip_id": trip["id"]},
        )
        connection.commit()

        error = assert_error(future.result(timeout=5), 409, "VERSION_CONFLICT")
        assert error["details"]["current"]["startsOn"] == "2030-06-01"
        assert error["details"]["current"]["version"] == 2
    finally:
        connection.rollback()
        connection.close()
        pool.shutdown(wait=True, cancel_futures=True)


def test_24_concurrent_patches_have_one_winner_and_complete_current_state(app_client, db_engine):
    trip = create_trip(app_client)
    bodies = [
        {
            "version": trip["version"],
            "destinations": [f"Destination {index}", "Return city"],
            "budgetUsd": f"100.{index:02d}",
        }
        for index in range(24)
    ]
    install_delay_trigger(db_engine, "test_delay_trip_update", "UPDATE", "trips")
    try:
        responses = run_concurrently(
            [
                lambda body=body: app_client.patch(f"/trips/{trip['id']}", json=body)
                for body in bodies
            ]
        )
    finally:
        remove_delay_trigger(db_engine, "test_delay_trip_update", "trips")
    successes = [response for response in responses if response.status_code == 200]
    conflicts = [response for response in responses if response.status_code == 409]
    assert len(successes) == 1
    assert len(conflicts) == 23
    assert all(response.json()["code"] == "VERSION_CONFLICT" for response in conflicts)
    assert all(response.status_code != 500 for response in responses)

    winner = successes[0].json()
    final = app_client.get(f"/trips/{trip['id']}").json()
    assert final == winner
    assert final["version"] == trip["version"] + 1
    winning_body = next(body for body in bodies if body["budgetUsd"] == final["budgetUsd"])
    assert final["destinations"] == winning_body["destinations"]
    assert all(response.json()["details"]["current"] == final for response in conflicts)


def test_24_concurrent_adds_create_exactly_one_participant(app_client, db_engine):
    trip = create_trip(app_client)
    install_delay_trigger(
        db_engine, "test_delay_participant_insert", "INSERT", "trip_participants"
    )
    try:
        responses = run_concurrently(
            [
                lambda: app_client.post(
                    f"/trips/{trip['id']}/participants",
                    json={"userId": "ben", "role": "viewer"},
                )
                for _ in range(24)
            ]
        )
    finally:
        remove_delay_trigger(db_engine, "test_delay_participant_insert", "trip_participants")
    assert sum(response.status_code == 201 for response in responses) == 1
    assert sum(response.status_code == 409 for response in responses) == 23
    assert all(response.status_code != 500 for response in responses)
    assert all(
        response.json()["code"] == "PARTICIPANT_EXISTS"
        for response in responses
        if response.status_code == 409
    )
    with db_engine.connect() as connection:
        count = connection.scalar(
            text(
                "SELECT count(*) FROM trip_participants "
                "WHERE trip_id = :trip_id AND user_id = 'ben'"
            ),
            {"trip_id": trip["id"]},
        )
    assert count == 1
    current = app_client.get(f"/trips/{trip['id']}").json()
    assert current["version"] == 1
    assert current["participants"] == [
        {"userId": "ana", "role": "organizer"},
        {"userId": "ben", "role": "viewer"},
    ]
