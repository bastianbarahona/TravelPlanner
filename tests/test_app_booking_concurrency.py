"""Iteration 4 required tests 5, 6 and 7 under real concurrency."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text

from app_support import (
    ProviderStub,
    StubResponse,
    app_client as make_app_client,
    assert_error,
    create_planning_trip,
    install_delay_trigger,
    provider_error,
    remove_delay_trigger,
    stub_offer,
)
from booking_support import (
    add_item,
    attempt_count,
    book_item,
    delete_item,
    get_booking,
    get_itinerary,
    is_offer_read,
    posts,
    provider_booking,
    wait_until,
)
from support import run_concurrently


@pytest.mark.parametrize("hold", [True, False], ids=["winner-held-at-provider", "winner-answers-fast"])
def test_24_concurrent_bookings_make_one_attempt_and_one_provider_post(start_app, db_engine, hold):
    offer = stub_offer("race-offer")
    release = threading.Event()
    if not hold:
        release.set()

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        assert release.wait(20)
        return StubResponse(201, provider_booking(request, offer))

    with ProviderStub(respond) as stub:
        process = start_app(
            PROVIDER_BASE_URL=stub.url, PROVIDER_TIMEOUT_SECONDS="30", BOOKING_POLL_ENABLED="0"
        )
        with make_app_client(process.url) as client, ThreadPoolExecutor(24) as pool:
            trip = create_planning_trip(client)
            _, item = add_item(client, trip["id"], 1, offer["id"])
            barrier = Barrier(24)

            def call():
                barrier.wait()
                return book_item(client, trip["id"], item)

            futures = [pool.submit(call) for _ in range(24)]
            try:
                if hold:
                    # Every loser is answered while the winner is still waiting on the provider.
                    wait_until(
                        lambda: sum(future.done() for future in futures) == 23,
                        "23 losers to be answered while the winner waits",
                    )
            finally:
                release.set()
            responses = [future.result(timeout=25) for future in futures]

            winners = [response for response in responses if response.status_code == 201]
            losers = [response for response in responses if response.status_code != 201]
            assert len(winners) == 1, [response.status_code for response in responses]
            winner = winners[0].json()
            assert winner["attempt"] == 1
            expected_codes = {"BOOKING_IN_PROGRESS"} if hold else {"BOOKING_IN_PROGRESS", "ALREADY_BOOKED"}
            for loser in losers:
                assert loser.status_code == 409, loser.text
                error = loser.json()
                assert error["code"] in expected_codes
                current = error["details"]["current"]
                assert current["id"] == winner["id"]
                assert current["clientReference"] == winner["clientReference"]
            assert attempt_count(db_engine, item["id"]) == 1
            assert len(posts(stub)) == 1
            assert get_booking(client, trip["id"], item["id"]).json()["state"] == "confirmed"


def add_offer(client, trip_id, version, offer_id):
    return client.post(
        f"/trips/{trip_id}/itinerary/items",
        json={"version": version, "offerId": offer_id},
    )


def test_24_concurrent_adds_of_one_offer_have_one_winner_and_one_item(start_app, db_engine):
    # Same version on every add, so this proves one winner and one item. The version check settles the race
    # before the offer rule is reached: uq_itinerary_offer is proven by SQL in test_app_migration_0003.py, and
    # the OFFER_ALREADY_IN_ITINERARY code by the test below.
    with ProviderStub(lambda request: StubResponse(200, stub_offer("one-offer"))) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            install_delay_trigger(db_engine, "test_delay_same_offer", "UPDATE", "trips")
            try:
                responses = run_concurrently(
                    [lambda: add_offer(client, trip["id"], 1, "one-offer") for _ in range(24)]
                )
            finally:
                remove_delay_trigger(db_engine, "test_delay_same_offer", "trips")
            final = get_itinerary(client, trip["id"])
            winners = [response for response in responses if response.status_code == 201]
            assert len(winners) == 1
            assert winners[0].json() == final
            for loser in responses:
                if loser.status_code != 201:
                    assert loser.status_code == 409, loser.text
                    error = loser.json()
                    assert error["code"] in {"VERSION_CONFLICT", "OFFER_ALREADY_IN_ITINERARY"}
                    assert error["details"]["current"] == final
            assert len(final["items"]) == 1
            with db_engine.connect() as connection:
                assert connection.scalar(
                    text("SELECT count(*) FROM itinerary_items WHERE trip_id = :id"),
                    {"id": trip["id"]},
                ) == 1


def test_24_concurrent_adds_of_an_offer_already_in_the_trip_are_all_refused(start_app, db_engine):
    with ProviderStub(lambda request: StubResponse(200, stub_offer("present-offer"))) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            current, _ = add_item(client, trip["id"], 1, "present-offer")
            responses = run_concurrently(
                [lambda: add_offer(client, trip["id"], current["version"], "present-offer") for _ in range(24)]
            )
            for response in responses:
                error = assert_error(response, 409, "OFFER_ALREADY_IN_ITINERARY")
                assert error["details"]["current"] == current
            assert get_itinerary(client, trip["id"]) == current


# One stub answer per final state of the first attempt.
ATTEMPT_STATES = {
    "unknown": lambda request, offer: StubResponse(503, provider_error("PROVIDER_UNAVAILABLE")),
    "pending": lambda request, offer: StubResponse(201, provider_booking(request, offer, "pending")),
    "confirmed": lambda request, offer: StubResponse(201, provider_booking(request, offer)),
    "rejected": lambda request, offer: StubResponse(409, provider_error("OFFER_CHANGED")),
    "failed": lambda request, offer: StubResponse(201, provider_booking(request, offer, "failed")),
}


@pytest.mark.parametrize("state", ATTEMPT_STATES)
def test_delete_is_refused_while_an_attempt_is_open_or_confirmed_and_allowed_once_closed(
    start_app, db_engine, state
):
    offer = stub_offer(f"delete-{state}-offer")

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        return ATTEMPT_STATES[state](request, offer)

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            trip = create_planning_trip(client)
            view, item = add_item(client, trip["id"], 1, offer["id"])
            assert book_item(client, trip["id"], item).json()["state"] == state

            version = get_itinerary(client, trip["id"])["version"]
            response = delete_item(client, trip["id"], item["id"], version)
            if state in {"unknown", "pending", "confirmed"}:
                error = assert_error(response, 409, "ITEM_HAS_BOOKING")
                current = get_itinerary(client, trip["id"])
                assert error["details"]["current"] == current
                assert current["version"] == version
                assert [entry["id"] for entry in current["items"]] == [item["id"]]
                assert attempt_count(db_engine, item["id"]) == 1
            else:
                assert response.status_code == 200, response.text
                assert response.json()["items"] == []
                assert attempt_count(db_engine, item["id"]) == 0
                again, _ = add_item(client, trip["id"], response.json()["version"], offer["id"])
                assert len(again["items"]) == 1


def test_concurrent_delete_and_book_never_both_succeed_or_leave_an_orphan(start_app, db_engine):
    rounds = 20

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, stub_offer(request["path"].rsplit("/", 1)[1]))
        offer = stub_offer(request["body"]["offerId"])
        return StubResponse(201, provider_booking(request, offer))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            setups = []
            for index in range(rounds):
                trip = create_planning_trip(client)
                view, item = add_item(client, trip["id"], 1, f"race-delete-offer-{index}")
                setups.append((trip, view["version"], item))

            # The delay widens the window between the delete's check and its commit.
            install_delay_trigger(db_engine, "test_delay_delete_race", "UPDATE", "trips")
            try:
                outcomes = []
                for index, (trip, version, item) in enumerate(setups):
                    lead = (0, 0.05, -0.05, 0.1, -0.1)[index % 5]  # who starts first, so both orders occur

                    def delete():
                        time.sleep(max(lead, 0))
                        return delete_item(client, trip["id"], item["id"], version)

                    def book():
                        time.sleep(max(-lead, 0))
                        return book_item(client, trip["id"], item)

                    outcomes.append(run_concurrently([delete, book]))
            finally:
                remove_delay_trigger(db_engine, "test_delay_delete_race", "trips")

            for (trip, _version, item), (deleted, booked) in zip(setups, outcomes):
                detail = (deleted.status_code, deleted.text, booked.status_code, booked.text)
                assert (deleted.status_code == 200) != (booked.status_code == 201), detail
                with db_engine.connect() as connection:
                    item_exists = connection.scalar(
                        text("SELECT count(*) FROM itinerary_items WHERE id = :id"), {"id": item["id"]}
                    )
                sent = [r for r in posts(stub) if r["body"]["offerId"] == item["offer"]["id"]]
                if deleted.status_code == 200:
                    assert_error(booked, 404, "NOT_FOUND")
                    assert item_exists == 0
                    assert attempt_count(db_engine, item["id"]) == 0
                    assert sent == []
                else:
                    assert_error(deleted, 409, "ITEM_HAS_BOOKING")
                    assert item_exists == 1
                    assert attempt_count(db_engine, item["id"]) == 1
                    assert len(sent) == 1
            # The delete must have lost to a booking at least once, so the refusal path really ran.
            assert any(
                deleted.status_code == 409 and deleted.json()["code"] == "ITEM_HAS_BOOKING"
                for deleted, _booked in outcomes
            )
            with db_engine.connect() as connection:
                orphans = connection.scalar(
                    text(
                        "SELECT count(*) FROM booking_attempts a WHERE NOT EXISTS "
                        "(SELECT 1 FROM itinerary_items i WHERE i.id = a.item_id)"
                    )
                )
            assert orphans == 0


def test_item_deleted_while_the_attempt_insert_is_in_flight_makes_the_booking_a_404(start_app, db_engine):
    offer = stub_offer("fk-race-offer")

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        return StubResponse(201, provider_booking(request, offer))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client, ThreadPoolExecutor(1) as pool:
            trip = create_planning_trip(client)
            view, item = add_item(client, trip["id"], 1, offer["id"])
            # The trigger holds the insert for 0.3 s, before the foreign key looks at the item.
            install_delay_trigger(db_engine, "test_delay_attempt_insert", "INSERT", "booking_attempts")
            try:
                booking = pool.submit(book_item, client, trip["id"], item)

                def insert_in_flight():
                    with db_engine.connect() as connection:
                        return connection.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
                                "AND state = 'active' AND query ILIKE '%INSERT INTO booking_attempts%'"
                            )
                        )

                wait_until(insert_in_flight, "the attempt insert to be in flight")
                deleted = delete_item(client, trip["id"], item["id"], view["version"])
                answer = booking.result(timeout=15)
            finally:
                remove_delay_trigger(db_engine, "test_delay_attempt_insert", "booking_attempts")
            assert deleted.status_code == 200, deleted.text
            assert_error(answer, 404, "NOT_FOUND")
            assert attempt_count(db_engine, item["id"]) == 0
            assert posts(stub) == []


@pytest.mark.parametrize("answer", ["failed", "offer-unavailable"])
def test_concurrent_bookings_never_post_twice_when_the_winner_closes_at_once(start_app, db_engine, answer):
    """A loser that lost the insert must never start attempt 2 behind a winner that already failed or was rejected."""
    offer = stub_offer(f"closing-{answer}-offer")
    rounds = 3

    def respond(request):
        if is_offer_read(request):
            return StubResponse(200, offer)
        if answer == "failed":
            return StubResponse(201, provider_booking(request, offer, "failed"))
        return StubResponse(409, provider_error("OFFER_UNAVAILABLE"))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url, BOOKING_POLL_ENABLED="0")
        with make_app_client(process.url) as client:
            setups = []
            for _ in range(rounds):
                trip = create_planning_trip(client)
                _, item = add_item(client, trip["id"], 1, offer["id"])
                setups.append((trip, item))
            # Staged delay: the first insert to wake commits after 1 s, every other one reaches the unique index only
            # after 2.5 s, when the winner has long since been answered and closed. Every racer has read "no attempt"
            # before the winner commits (they arrive within about 0.5 s), so each one is a loser of the insert, and
            # the old behaviour, a loser starting attempt 2 behind the closed winner, would show as a second POST.
            # A racer that starts after the winner closed may legitimately begin attempt 2 (rule 3): there are none
            # here. Twelve racers, not 24: each holds a pooled connection (5 + 10 overflow) through its delayed
            # insert, and a 16th would wait for a connection and be a late starter, not a loser of the insert.
            with db_engine.begin() as connection:
                connection.execute(text("CREATE SEQUENCE test_closing_insert_seq"))
                connection.execute(
                    text(
                        "CREATE FUNCTION test_closing_insert_delay() RETURNS trigger LANGUAGE plpgsql AS $$ "
                        "BEGIN PERFORM pg_sleep(CASE WHEN nextval('test_closing_insert_seq') = 1 THEN 1.0 ELSE 2.5 END); "
                        "RETURN NEW; END $$"
                    )
                )
                connection.execute(
                    text(
                        "CREATE TRIGGER test_closing_insert_delay BEFORE INSERT ON booking_attempts "
                        "FOR EACH ROW EXECUTE FUNCTION test_closing_insert_delay()"
                    )
                )
            try:
                for index, (trip, item) in enumerate(setups, start=1):
                    with db_engine.begin() as connection:
                        connection.execute(text("ALTER SEQUENCE test_closing_insert_seq RESTART"))
                    responses = run_concurrently([lambda: book_item(client, trip["id"], item) for _ in range(12)])
                    winners = [response for response in responses if response.status_code == 201]
                    assert len(winners) == 1, [(r.status_code, r.text[:80]) for r in responses]
                    winner = winners[0].json()
                    assert winner["state"] == ("failed" if answer == "failed" else "rejected")
                    for loser in responses:
                        if loser.status_code == 201:
                            continue
                        assert loser.status_code == 409, loser.text
                        error = loser.json()
                        assert error["code"] in {"BOOKING_IN_PROGRESS", "ALREADY_BOOKED", "BOOKING_CONFLICT"}, error
                        assert error["details"]["current"]["id"] == winner["id"], error
                    assert attempt_count(db_engine, item["id"]) == 1
                    assert len(posts(stub)) == index  # exactly one provider POST per item, ever
            finally:
                with db_engine.begin() as connection:
                    connection.execute(text("DROP TRIGGER IF EXISTS test_closing_insert_delay ON booking_attempts"))
                    connection.execute(text("DROP FUNCTION IF EXISTS test_closing_insert_delay()"))
                    connection.execute(text("DROP SEQUENCE IF EXISTS test_closing_insert_seq"))
