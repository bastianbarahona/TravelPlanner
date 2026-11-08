"""Iteration 4 required test 11: migration 0003 and the constraints that protect bookings."""
import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app_support import assert_constraint, insert_trip, run_alembic, stub_offer

INSERT_ITEM = (
    "INSERT INTO itinerary_items (id, trip_id, position, offer) "
    "VALUES (:id, :trip_id, :position, CAST(:offer AS jsonb))"
)
# Every column the brief lists is given, so a failure can only come from the constraint under test.
INSERT_ATTEMPT = (
    "INSERT INTO booking_attempts (id, trip_id, item_id, attempt_no, idempotency_key, "
    "client_reference, request, state, requested_by, created_at, next_check_at, check_count) "
    "VALUES (:id, :trip_id, :item_id, :attempt_no, :key, :reference, CAST(:request AS jsonb), "
    ":state, 'ana', now(), now(), 0)"
)


def new_item(engine, trip_id, offer_id=None, position=0):
    item_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(INSERT_ITEM),
            {
                "id": item_id,
                "trip_id": trip_id,
                "position": position,
                "offer": json.dumps(stub_offer(offer_id or f"offer-{uuid4().hex}")),
            },
        )
    return item_id


def attempt_params(trip_id, item_id, attempt_no=1, state="unknown"):
    return {
        "id": uuid4(),
        "trip_id": trip_id,
        "item_id": item_id,
        "attempt_no": attempt_no,
        "key": uuid4().hex,
        "reference": f"trip-{trip_id}-item-{item_id}-attempt-{attempt_no}",
        "request": json.dumps({"clientReference": "x"}),
        "state": state,
    }


def add_attempt(engine, trip_id, item_id, attempt_no=1, state="unknown"):
    with engine.begin() as connection:
        connection.execute(text(INSERT_ATTEMPT), attempt_params(trip_id, item_id, attempt_no, state))


def trip_with_item(engine):
    trip_id = uuid4()
    insert_trip(engine, trip_id)
    return trip_id, new_item(engine, trip_id)


@contextmanager
def scratch_database(postgres):
    database = f"migration_{uuid4().hex}"
    base = postgres.database_url.rsplit("/", 1)[0]
    admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    engine = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        engine = create_engine(f"{base}/{database}")
        yield f"{base}/{database}", engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database AND pid <> pg_backend_pid()"
                ),
                {"database": database},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
        admin.dispose()


def test_0003_upgrades_a_populated_0002_database_with_no_drift(postgres, empty_database):
    with scratch_database(postgres) as (url, engine):
        before = run_alembic(url, "upgrade", "0002")
        assert before.returncode == 0, before.stdout + before.stderr
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('booking_attempts') IS NOT NULL")) is False
        trip_id, item_id = uuid4(), uuid4()
        insert_trip(engine, trip_id)
        with engine.begin() as connection:
            connection.execute(
                text(INSERT_ITEM),
                {"id": item_id, "trip_id": trip_id, "position": 0, "offer": json.dumps(stub_offer())},
            )

        head = run_alembic(url, "upgrade", "head")
        assert head.returncode == 0, head.stdout + head.stderr
        check = run_alembic(url, "check")
        assert check.returncode == 0, check.stdout + check.stderr
        # Holds for any later head: one linear head, the database at it, and 0003 on the path from 0002.
        heads = run_alembic(url, "heads").stdout.strip().splitlines()
        assert len(heads) == 1 and heads[0].endswith("(head)"), heads
        assert "(head)" in run_alembic(url, "current").stdout
        assert "0002 -> 0003" in run_alembic(url, "history").stdout

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('booking_attempts') IS NOT NULL")) is True
            assert connection.scalar(
                text("SELECT count(*) FROM itinerary_items WHERE id = :id"), {"id": item_id}
            ) == 1
        add_attempt(engine, trip_id, item_id)  # the new table is usable on the migrated data


@pytest.mark.parametrize("open_state", ["unknown", "pending", "confirmed"])
def test_uq_one_open_attempt_refuses_a_second_attempt_while_one_is_open_or_confirmed(
    empty_database, open_state
):
    trip_id, item_id = trip_with_item(empty_database)
    add_attempt(empty_database, trip_id, item_id, 1, open_state)
    assert_constraint(
        empty_database,
        INSERT_ATTEMPT,
        attempt_params(trip_id, item_id, 2, "unknown"),
        "uq_one_open_attempt",
    )


def test_uq_booking_attempt_number_refuses_a_repeated_number_for_one_item(empty_database):
    trip_id, item_id = trip_with_item(empty_database)
    add_attempt(empty_database, trip_id, item_id, 1, "rejected")
    assert_constraint(
        empty_database,
        INSERT_ATTEMPT,
        # A distinct key and reference, so only (item_id, attempt_no) can collide.
        attempt_params(trip_id, item_id, 1, "rejected") | {"reference": f"other-{uuid4().hex}"},
        "uq_booking_attempt_number",
    )


@pytest.mark.parametrize("closed_state", ["rejected", "failed"])
def test_uq_one_open_attempt_allows_a_new_attempt_after_rejected_or_failed(
    empty_database, closed_state
):
    trip_id, item_id = trip_with_item(empty_database)
    add_attempt(empty_database, trip_id, item_id, 1, closed_state)
    add_attempt(empty_database, trip_id, item_id, 2, "unknown")
    assert_constraint(
        empty_database,
        INSERT_ATTEMPT,
        attempt_params(trip_id, item_id, 3, "pending"),
        "uq_one_open_attempt",
    )
    other_item = new_item(empty_database, trip_id, position=1)
    add_attempt(empty_database, trip_id, other_item, 1, "unknown")  # the limit is per item


def test_attempt_foreign_key_to_the_item_is_restrict_and_blocks_the_delete(empty_database):
    trip_id, item_id = trip_with_item(empty_database)
    add_attempt(empty_database, trip_id, item_id)
    with empty_database.connect() as connection:
        actions = connection.execute(
            text(
                "SELECT conname || ':' || confdeltype::text FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid = 'booking_attempts'::regclass "
                "AND confrelid = 'itinerary_items'::regclass"
            )
        ).scalars().all()
    assert actions == ["booking_attempts_item_id_fkey:r"]

    with pytest.raises(IntegrityError) as caught:
        with empty_database.begin() as connection:
            connection.execute(text("DELETE FROM itinerary_items WHERE id = :id"), {"id": item_id})
    assert caught.value.orig.sqlstate == "23503"
    with empty_database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM itinerary_items WHERE id = :id"), {"id": item_id}) == 1
        assert connection.scalar(text("SELECT count(*) FROM booking_attempts WHERE item_id = :id"), {"id": item_id}) == 1


def test_attempt_state_check_refuses_an_unknown_state_and_accepts_the_five_real_ones(empty_database):
    trip_id, item_id = trip_with_item(empty_database)
    with pytest.raises(IntegrityError) as caught:
        add_attempt(empty_database, trip_id, item_id, 1, "booked")
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == "ck_booking_attempts_state"

    for position, state in enumerate(("unknown", "pending", "confirmed", "failed", "rejected"), start=1):
        add_attempt(empty_database, trip_id, new_item(empty_database, trip_id, position=position), 1, state)


def test_uq_itinerary_offer_refuses_the_same_offer_twice_in_one_trip_only(empty_database):
    trip_id = uuid4()
    insert_trip(empty_database, trip_id)
    new_item(empty_database, trip_id, "same-offer", position=0)
    with pytest.raises(IntegrityError) as caught:
        new_item(empty_database, trip_id, "same-offer", position=1)
    assert caught.value.orig.diag.constraint_name == "uq_itinerary_offer"

    new_item(empty_database, trip_id, "another-offer", position=1)  # a different offer is fine
    other_trip = uuid4()
    insert_trip(empty_database, other_trip)
    new_item(empty_database, other_trip, "same-offer", position=0)  # the same offer in another trip is fine
