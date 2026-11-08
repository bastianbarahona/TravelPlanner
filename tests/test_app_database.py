"""Requirement 10: migration parity and database-enforced invariants."""
from uuid import uuid4

from sqlalchemy import create_engine, text

from app_support import assert_constraint, insert_trip, run_alembic


def test_alembic_upgrade_and_check_on_a_fresh_database(postgres, empty_database):
    database = f"migration_{uuid4().hex}"
    admin_url = postgres.database_url.rsplit("/", 1)[0] + "/postgres"
    test_url = postgres.database_url.rsplit("/", 1)[0] + f"/{database}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))

        upgrade = run_alembic(test_url, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
        check = run_alembic(test_url, "check")
        assert check.returncode == 0, check.stdout + check.stderr
    finally:
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


def test_ck_trips_dates_refuses_reversed_dates(empty_database):
    assert_constraint(
        empty_database,
        "INSERT INTO trips (id, starts_on, ends_on, budget_usd, travelers, version) "
        "VALUES (:id, '2030-06-13', '2030-06-10', 1.00, 2, 1)",
        {"id": uuid4()},
        "ck_trips_dates",
    )


def test_ck_trips_budget_refuses_negative_amount(empty_database):
    assert_constraint(
        empty_database,
        "INSERT INTO trips (id, starts_on, ends_on, budget_usd, travelers, version) "
        "VALUES (:id, '2030-06-10', '2030-06-13', -0.01, 2, 1)",
        {"id": uuid4()},
        "ck_trips_budget",
    )


def test_ck_participants_role_refuses_unknown_role(empty_database):
    trip_id = uuid4()
    insert_trip(empty_database, trip_id)
    assert_constraint(
        empty_database,
        "INSERT INTO trip_participants (trip_id, user_id, role) "
        "VALUES (:id, 'ana', 'owner')",
        {"id": trip_id},
        "ck_participants_role",
    )


def test_uq_one_organizer_refuses_a_second_organizer(empty_database):
    trip_id = uuid4()
    insert_trip(empty_database, trip_id)
    with empty_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO trip_participants (trip_id, user_id, role) "
                "VALUES (:id, 'ana', 'organizer')"
            ),
            {"id": trip_id},
        )
        connection.execute(
            text(
                "INSERT INTO trip_participants (trip_id, user_id, role) "
                "VALUES (:id, 'cam', 'viewer')"
            ),
            {"id": trip_id},
        )

    assert_constraint(
        empty_database,
        "INSERT INTO trip_participants (trip_id, user_id, role) "
        "VALUES (:id, 'ben', 'organizer')",
        {"id": trip_id},
        "uq_one_organizer",
    )
