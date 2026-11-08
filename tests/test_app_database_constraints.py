"""Iteration 3 requirement 13: migration constraints for travelers and itinerary."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app_support import (
    PostgresProcess,
    assert_constraint,
    insert_trip,
    run_alembic,
    stub_offer,
)


@pytest.mark.parametrize("travelers", [0, 21])
def test_ck_trips_travelers_refuses_values_outside_one_to_twenty(
    empty_database, travelers
):
    assert_constraint(
        empty_database,
        "INSERT INTO trips (id, starts_on, ends_on, budget_usd, travelers, version) "
        "VALUES (:id, '2030-06-10', '2030-06-13', 1.00, :travelers, 1)",
        {"id": uuid4(), "travelers": travelers},
        "ck_trips_travelers",
    )


def test_itinerary_position_unique_constraint_is_deferred_and_checked_at_commit(
    empty_database,
):
    with empty_database.connect() as connection:
        constraint = connection.execute(
            text(
                "SELECT condeferrable, condeferred FROM pg_constraint "
                "WHERE conname = 'uq_itinerary_position'"
            )
        ).one()
    assert tuple(constraint) == (True, True)

    trip_id = uuid4()
    insert_trip(empty_database, trip_id)
    with pytest.raises(IntegrityError) as caught:
        with empty_database.begin() as connection:
            for index in range(2):
                connection.execute(
                    text(
                        "INSERT INTO itinerary_items (id, trip_id, position, offer) "
                        "VALUES (:id, :trip_id, 0, CAST(:offer AS jsonb))"
                    ),
                    {"id": uuid4(), "trip_id": trip_id, "offer": json.dumps(stub_offer(f"offer-{index}"))},
                )
    assert caught.value.orig.diag.constraint_name == "uq_itinerary_position"


def test_itinerary_trip_foreign_key_refuses_an_orphan(empty_database):
    with pytest.raises(IntegrityError) as caught:
        with empty_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO itinerary_items (id, trip_id, position, offer) "
                    "VALUES (:id, :trip_id, 0, CAST(:offer AS jsonb))"
                ),
                {
                    "id": uuid4(),
                    "trip_id": uuid4(),
                    "offer": json.dumps(stub_offer()),
                },
            )
    assert caught.value.orig.diag.constraint_name
    assert "trip" in caught.value.orig.diag.constraint_name


def test_0002_backfills_travelers_and_leaves_no_default_on_a_fresh_database():
    postgres = PostgresProcess()
    engine = None
    try:
        postgres.start()
        first = run_alembic(postgres.database_url, "upgrade", "0001")
        assert first.returncode == 0, first.stdout + first.stderr
        engine = create_engine(postgres.database_url)
        existing_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO trips (id, starts_on, ends_on, budget_usd, version) "
                    "VALUES (:id, '2030-06-10', '2030-06-13', 1.00, 1)"
                ),
                {"id": existing_id},
            )

        head = run_alembic(postgres.database_url, "upgrade", "head")
        assert head.returncode == 0, head.stdout + head.stderr
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT travelers FROM trips WHERE id = :id"),
                {"id": existing_id},
            ) == 1

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO trips (id, starts_on, ends_on, budget_usd, version) "
                        "VALUES (:id, '2030-06-10', '2030-06-13', 1.00, 1)"
                    ),
                    {"id": uuid4()},
                )
    finally:
        if engine is not None:
            engine.dispose()
        postgres.kill()
