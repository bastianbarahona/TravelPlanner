"""Iteration 5, US-09 and US-10: documents, written from docs/iteration-5-brief.md alone.

Every app here comes from start_app(**env): the session `application` fixture runs with the
document worker off, so no stray worker can claim a document a test wants to see untouched.
"""
import hashlib
import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app_support import (
    ProviderStub,
    StubResponse,
    add_participant,
    app_client as make_app_client,
    assert_constraint,
    assert_error,
    create_planning_trip,
    insert_trip,
    stub_offer,
    stub_search_result,
)

ABSENT_WHEN_NOT_COMPLETE = {"completedAt", "sha256", "sizeBytes"}
INSERT_DOCUMENT = (
    "INSERT INTO documents (id, trip_id, trip_version, source, source_sha256, state, requested_by, "
    "attempts, lease_token, lease_until, content, content_sha256, completed_at) "
    "VALUES (:id, :trip_id, :trip_version, CAST(:source AS jsonb), :source_sha256, :state, "
    ":requested_by, :attempts, :lease_token, :lease_until, :content, :content_sha256, :completed_at)"
)


# --- helpers -----------------------------------------------------------------------------------


def add(client, trip_id, version, offer_id):
    response = client.post(
        f"/trips/{trip_id}/itinerary/items", json={"version": version, "offerId": offer_id}
    )
    assert response.status_code == 201, response.text
    return response.json()


def two_item_trip(client, **overrides):
    """A trip with a hotel and a free activity: itinerary version 3."""
    trip = create_planning_trip(client, **overrides)
    add(client, trip["id"], 1, "offer-hotel-001")
    itinerary = add(client, trip["id"], 2, "offer-activity-001")
    assert itinerary["version"] == 3 and len(itinerary["items"]) == 2
    return trip, itinerary


def path(trip_id, document_id=None, suffix=""):
    base = f"/trips/{trip_id}/documents"
    return base if document_id is None else f"{base}/{document_id}{suffix}"


def request_document(client, trip_id):
    return client.post(path(trip_id))


def get_view(client, trip_id, document_id):
    response = client.get(path(trip_id, document_id))
    assert response.status_code == 200, response.text
    return response.json()


def wait_state(client, trip_id, document_id, state, timeout=30, seen=None):
    """Polls the state endpoint until `state`; every observed view goes to `seen`."""
    deadline = time.monotonic() + timeout
    while True:
        view = get_view(client, trip_id, document_id)
        if seen is not None:
            seen.append(view)
        if view["state"] == state:
            return view
        assert time.monotonic() < deadline, f"document stayed {view['state']}, wanted {state}: {view}"
        time.sleep(0.05)


def complete_document(client, trip_id):
    created = request_document(client, trip_id)
    assert created.status_code == 202, created.text
    return wait_state(client, trip_id, created.json()["id"], "complete")


def download(client, trip_id, document_id):
    response = client.get(path(trip_id, document_id, "/content"))
    assert response.status_code == 200, response.text
    return response


def row(db_engine, document_id):
    with db_engine.connect() as connection:
        return (
            connection.execute(
                text(
                    "SELECT state, attempts, lease_token, lease_until, content, content_sha256, "
                    "completed_at, error, trip_version FROM documents WHERE id = CAST(:id AS uuid)"
                ),
                {"id": document_id},
            )
            .mappings()
            .one()
        )


def document_params(**overrides):
    source = overrides.get("source", "{}")
    return {
        "id": uuid4(),
        "trip_id": None,
        "trip_version": 1,
        "source": source,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "state": "waiting",
        "requested_by": "ana",
        "attempts": 0,
        "lease_token": None,
        "lease_until": None,
        "content": None,
        "content_sha256": None,
        "completed_at": None,
    } | overrides


def insert_document(db_engine, trip_id, **overrides):
    params = document_params(trip_id=UUID(trip_id), **overrides)
    with db_engine.begin() as connection:
        connection.execute(text(INSERT_DOCUMENT), params)
    return str(params["id"])


def without_request_id(response):
    return {key: value for key, value in assert_error(response, 404, "NOT_FOUND").items() if key != "requestId"}


# --- 1 and 2: lifecycle and content ------------------------------------------------------------


def test_request_waits_without_a_worker_survives_a_hard_kill_then_completes_with_a_matching_hash(
    start_app, db_engine, control
):
    """US-09: stored state is truth (waiting survives a kill, not-ready is 409); the file equals its record."""
    idle = start_app(DOCUMENT_WORKER_ENABLED="0")
    with make_app_client(idle.url) as client:
        trip, itinerary = two_item_trip(client)
        created = request_document(client, trip["id"])
        assert created.status_code == 202
        view = created.json()
        assert view["state"] == "waiting"
        assert view["tripId"] == trip["id"]
        assert view["tripVersion"] == itinerary["version"] == 3
        assert view["requestedBy"] == "ana"
        assert view["attempts"] == 0
        assert ABSENT_WHEN_NOT_COMPLETE.isdisjoint(view) and "error" not in view
        assert "queued" in view["nextStep"].lower()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:  # absence over time: it must not move without a worker
            assert get_view(client, trip["id"], view["id"]) == view
            time.sleep(0.2)
        not_ready = client.get(path(trip["id"], view["id"], "/content"))
        body = assert_error(not_ready, 409, "DOCUMENT_NOT_READY")
        assert body["details"]["current"] == view
    idle.kill()
    idle.start()
    with make_app_client(idle.url) as client:
        assert get_view(client, trip["id"], view["id"]) == view
    idle.kill()

    worker = start_app(DOCUMENT_RENDER_DELAY_SECONDS="2")
    with make_app_client(worker.url) as client:
        seen = []
        done = wait_state(client, trip["id"], view["id"], "complete", seen=seen)
        states = [observed["state"] for observed in seen]
        assert "in_progress" in states, states
        assert states.index("in_progress") < states.index("complete")
        assert "failed" not in states
        assert "being generated" in next(v for v in seen if v["state"] == "in_progress")["nextStep"].lower()
        assert done["attempts"] == 1
        assert "error" not in done
        assert "ready" in done["nextStep"].lower()

        response = download(client, trip["id"], view["id"])
        assert response.headers["content-type"].lower() == "text/html; charset=utf-8"
        assert response.headers["content-disposition"] == (
            f'attachment; filename="trip-{trip["id"]}-v3-{view["id"]}.html"'
        )
        assert response.headers["content-security-policy"] == "default-src 'none'"
        assert response.headers["x-content-type-options"] == "nosniff"
        digest = hashlib.sha256(response.content).hexdigest()
        stored = row(db_engine, view["id"])
        assert done["sha256"] == digest == stored["content_sha256"]
        assert done["sizeBytes"] == len(response.content) == len(bytes(stored["content"]))
        assert bytes(stored["content"]) == response.content
        assert stored["state"] == "complete" and stored["completed_at"] is not None
        assert done["completedAt"]


def test_content_has_every_required_text_and_escapes_user_and_provider_text(start_app, db_engine, control):
    """US-09, US-10: the HTML carries the brief's texts, escaped; the stored source has only the brief's keys."""
    hostile_title = 'Hotel <zz-marker>&"quoted"</zz-marker>'
    control.change_offer("offer-hotel-001", title=hostile_title)
    hostile_destination = "<script>alert(1)</script>"
    process = start_app()
    with make_app_client(process.url) as client:
        trip, itinerary = two_item_trip(
            client, destinations=["Lisbon", "São Paulo", hostile_destination]
        )
        view = complete_document(client, trip["id"])
        raw = download(client, trip["id"], view["id"]).content
        page = raw.decode("utf-8")
    assert view["sizeBytes"] == len(raw) > len(page)  # bytes, not characters: "São" is multi-byte

    lowered = page.lower()
    assert "<!doctype html>" in lowered
    assert '<meta charset="utf-8">' in lowered
    for forbidden in ("<script", "<link", "src="):
        assert forbidden not in lowered
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", view["requestedAt"])

    def has(value):
        escaped = html.escape(value, quote=True)
        assert escaped in page, escaped

    has(f"Document {view['id']}")
    has(f"Trip version {itinerary['version']}")
    has(f"Captured at {view['requestedAt']}")
    for value in ("Lisbon", "São Paulo", hostile_destination):
        has(value)
    assert len(itinerary["items"]) == 2
    instants = ""
    for item in itinerary["items"]:
        offer = item["offer"]
        for value in (offer["title"], offer["category"], offer["startsAt"], offer["endsAt"]):
            has(value)
        instants += offer["startsAt"] + offer["endsAt"]
    # The trip dates may be prefixes of offer instants, so each needs one more occurrence than the offers give it.
    for date in (trip["startsOn"], trip["endsOn"]):
        assert page.count(date) >= instants.count(date) + 1, date

    budget = itinerary["budget"]
    labelled = [
        f"Estimated {budget['estimatedUsd']} USD",
        f"Confirmed {budget['confirmedUsd']} USD",
        f"Total {budget['totalUsd']} USD",
        f"Budget {trip['budgetUsd']} USD",
    ]
    for line in labelled:
        has(line)
    # "420.00 USD" is an item price, Estimated and Total; "0.00 USD" is a free item and Confirmed. Count each amount
    # as a whole number (not the tail of 420.00), so one label cannot stand in for another.
    amounts = [f"{item['offer']['price']['amount']} USD" for item in itinerary["items"]] + [
        line.split(" ", 1)[1] for line in labelled
    ]
    for amount in set(amounts):
        found = re.findall(r"(?<![\d.])" + re.escape(amount), page)
        assert len(found) >= amounts.count(amount), (amount, len(found), amounts.count(amount))
    assert page.count("Booking: not requested") == 2
    assert lowered.count("estimate") >= 4  # two priceKind values, the label and the disclaimer

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page
    assert "<zz-marker>" not in page
    assert re.search(r"quote", lowered), "no sentence about the provider's quotes"
    assert re.search(r"estimate[^.<]*\bnot\b[^.<]*confirmed", lowered), "no sentence: an estimate is not a confirmed price"

    with db_engine.connect() as connection:
        source, source_sha256 = connection.execute(
            text("SELECT source, source_sha256 FROM documents WHERE id = CAST(:id AS uuid)"), {"id": view["id"]}
        ).one()
    assert set(source) == {"trip", "items", "budget"}
    assert set(source["trip"]) == {"id", "destinations", "startsOn", "endsOn", "budgetUsd", "travelers", "version"}
    assert source["trip"]["version"] == view["tripVersion"] and source["trip"]["id"] == trip["id"]
    assert [item["id"] for item in source["items"]] == [item["id"] for item in itinerary["items"]]
    for item in source["items"]:  # no booking key: nothing is requested today
        assert set(item) == {"id", "position", "offer", "priceKind"}
    canonical = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert source_sha256 == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- 3 and 4: point in time and repeated requests ----------------------------------------------


def test_a_document_stays_the_trip_as_captured_and_a_new_request_captures_the_new_version(
    start_app, control
):
    """US-10: the snapshot is taken at request time; later itinerary edits neither touch it nor reuse it."""
    process = start_app()
    with make_app_client(process.url) as client:
        trip, _ = two_item_trip(client)
        add_participant(client, trip["id"], "ben", "viewer")
        before = client.get(f"/trips/{trip['id']}/itinerary").json()["version"]
        old = complete_document(client, trip["id"])
        old_bytes = download(client, trip["id"], old["id"]).content
        assert old["tripVersion"] == before
        assert f"Trip version {before}".encode() in old_bytes

        changed = add(client, trip["id"], before, "offer-hotel-002")
        assert changed["version"] == before + 1
        control.change_offer("offer-activity-001", title="Renamed after capture")

        created = request_document(client, trip["id"])
        assert created.status_code == 202
        assert created.json()["id"] != old["id"]
        assert created.json()["tripVersion"] == before + 1
        new = wait_state(client, trip["id"], created.json()["id"], "complete")
        new_bytes = download(client, trip["id"], new["id"]).content

        assert download(client, trip["id"], old["id"]).content == old_bytes
        assert f"Trip version {before + 1}".encode() in new_bytes
        assert f"Trip version {before + 1}".encode() not in old_bytes
        assert b"Renamed after capture" not in old_bytes and b"Renamed after capture" not in new_bytes
        assert get_view(client, trip["id"], old["id"]) == old
        assert new["sha256"] != old["sha256"]

        with make_app_client(process.url, "ben") as viewer:
            listed = viewer.get(path(trip["id"]))
            assert listed.status_code == 200
            assert [entry["id"] for entry in listed.json()["documents"]] == [new["id"], old["id"]]
            assert listed.json()["documents"][1]["tripVersion"] == before


def test_the_source_is_captured_when_requested_not_when_the_worker_renders(start_app, control):
    """US-10: an edit between the request and the render does not enter the document."""
    control.change_offer("offer-hotel-002", title="Added after the request")
    idle = start_app(DOCUMENT_WORKER_ENABLED="0")
    with make_app_client(idle.url) as client:
        trip, itinerary = two_item_trip(client)
        created = request_document(client, trip["id"])
        assert created.status_code == 202
        document_id = created.json()["id"]
        edited = add(client, trip["id"], itinerary["version"], "offer-hotel-002")
        assert edited["version"] == itinerary["version"] + 1
        assert get_view(client, trip["id"], document_id)["state"] == "waiting"
    idle.kill()

    worker = start_app()
    with make_app_client(worker.url) as client:
        done = wait_state(client, trip["id"], document_id, "complete")
        assert done["tripVersion"] == itinerary["version"]
        page = download(client, trip["id"], document_id).content.decode("utf-8")
    assert f"Trip version {itinerary['version']}" in page
    assert f"Trip version {edited['version']}" not in page
    assert "Added after the request" not in page


def test_repeated_requests_return_the_same_document_even_when_24_arrive_at_once(
    start_app, db_engine, control
):
    """US-09: unique (trip, source) + ON CONFLICT DO NOTHING: one row, one 202, never a 500."""
    process = start_app(DOCUMENT_WORKER_ENABLED="0")
    with make_app_client(process.url) as client:
        trip, _ = two_item_trip(client)
        add_participant(client, trip["id"], "ben", "editor")
        barrier = threading.Barrier(24)

        def request():
            barrier.wait(timeout=10)
            return request_document(client, trip["id"])

        with ThreadPoolExecutor(24) as pool:
            responses = list(pool.map(lambda _: request(), range(24)))
        assert sorted({response.status_code for response in responses}) in ([200, 202], [202])
        assert [response.status_code for response in responses].count(202) == 1
        ids = {response.json()["id"] for response in responses}
        assert len(ids) == 1
        with db_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT count(*) FROM documents WHERE trip_id = CAST(:id AS uuid)"), {"id": trip["id"]}
            ) == 1
        created = next(response for response in responses if response.status_code == 202).json()

        again = request_document(client, trip["id"])
        assert again.status_code == 200 and again.json() == created
        with make_app_client(process.url, "ben") as other_editor:
            from_ben = request_document(other_editor, trip["id"])
            assert from_ben.status_code == 200
            assert from_ben.json() == created
            assert from_ben.json()["requestedBy"] == "ana"
        with db_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM documents")) == 1


def test_the_source_pairs_a_version_with_its_own_items_when_an_edit_commits_mid_capture(
    start_app, db_engine, control
):
    """US-10, decision 3: the capture reads trip and items in ONE snapshot.

    The test holds ACCESS EXCLUSIVE on itinerary_items, so the capture reads the trip (version 3) and then blocks on
    the items. While it waits, one transaction adds an item and bumps the version, then commits. One snapshot gives
    (version 3, 2 items). If the capture read trip and items in separate snapshots (READ COMMITTED), it would give
    (version 3, 3 items): the old version paired with the new item, and this test fails.
    """
    process = start_app(DOCUMENT_WORKER_ENABLED="0")
    with make_app_client(process.url) as client:
        trip, itinerary = two_item_trip(client)
        before = itinerary["version"]
        connection = db_engine.connect()
        pool = ThreadPoolExecutor(1)
        try:
            connection.execute(text("LOCK TABLE itinerary_items IN ACCESS EXCLUSIVE MODE"))
            future = pool.submit(request_document, client, trip["id"])

            deadline = time.monotonic() + 10
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
                assert time.monotonic() < deadline, "the capture never waited on the itinerary_items lock"
                time.sleep(0.02)

            connection.execute(
                text(
                    "INSERT INTO itinerary_items (id, trip_id, position, offer) "
                    "SELECT :new_id, trip_id, 2, offer FROM itinerary_items WHERE trip_id = CAST(:trip_id AS uuid) "
                    "AND position = 0"
                ),
                {"new_id": uuid4(), "trip_id": trip["id"]},
            )
            connection.execute(
                text("UPDATE trips SET version = version + 1 WHERE id = CAST(:trip_id AS uuid)"),
                {"trip_id": trip["id"]},
            )
            connection.commit()
            response = future.result(timeout=15)
        finally:
            connection.rollback()
            connection.close()
            pool.shutdown(wait=True, cancel_futures=True)

        assert response.status_code == 202, response.text
        with db_engine.connect() as reader:
            source = reader.scalar(
                text("SELECT source FROM documents WHERE id = CAST(:id AS uuid)"), {"id": response.json()["id"]}
            )
        assert (source["trip"]["version"], len(source["items"])) == (before, 2)
        assert response.json()["tripVersion"] == before

        # Control: the edit did commit, so a later capture sees the new version with its three items.
        later = request_document(client, trip["id"])
        assert later.status_code == 202 and later.json()["tripVersion"] == before + 1
        with db_engine.connect() as reader:
            assert len(reader.scalar(
                text("SELECT source FROM documents WHERE id = CAST(:id AS uuid)"), {"id": later.json()["id"]}
            )["items"]) == 3


# --- 5 and 6: interruption and fence -----------------------------------------------------------


def test_a_kill_mid_generation_is_recovered_by_the_next_worker_and_nothing_reports_complete_early(
    start_app, db_engine, control
):
    """US-09: an expired lease is reclaimed (attempts 2); content exists only on a real complete."""
    slow = start_app(DOCUMENT_LEASE_SECONDS="1", DOCUMENT_RENDER_DELAY_SECONDS="4")
    with make_app_client(slow.url) as client:
        trip, _ = two_item_trip(client)
        created = request_document(client, trip["id"]).json()
        in_flight = wait_state(client, trip["id"], created["id"], "in_progress", timeout=20)
        assert in_flight["attempts"] == 1 and ABSENT_WHEN_NOT_COMPLETE.isdisjoint(in_flight)
    slow.kill()
    stored = row(db_engine, created["id"])
    assert stored["state"] == "in_progress" and stored["content"] is None and stored["completed_at"] is None

    fast = start_app(DOCUMENT_LEASE_SECONDS="1", DOCUMENT_RENDER_DELAY_SECONDS="0")
    with make_app_client(fast.url) as client:
        # 15 s: with the 1 s lease it is reclaimed within seconds; a hard-coded 30 s lease would time out here.
        done = wait_state(client, trip["id"], created["id"], "complete", timeout=15)
        assert done["attempts"] == 2
        response = download(client, trip["id"], created["id"])
        assert hashlib.sha256(response.content).hexdigest() == done["sha256"]


def test_a_claim_that_makes_the_fourth_attempt_fails_the_document_without_rendering_it(
    start_app, db_engine, control
):
    """US-09: attempts above 3 end failed with a reason, even though the source would render."""
    idle = start_app(DOCUMENT_WORKER_ENABLED="0")
    with make_app_client(idle.url) as client:
        trip, _ = two_item_trip(client)
        created = request_document(client, trip["id"]).json()  # a valid source, stored by the app
    with db_engine.begin() as connection:
        interrupted = connection.execute(
            text(
                "UPDATE documents SET state = 'in_progress', attempts = 3, lease_token = :token, "
                "lease_until = now() - interval '1 minute' WHERE id = CAST(:id AS uuid) AND state = 'waiting'"
            ),
            {"token": uuid4(), "id": created["id"]},
        )
        assert interrupted.rowcount == 1
    idle.kill()

    worker = start_app()
    with make_app_client(worker.url) as client:
        seen = []
        failed = wait_state(client, trip["id"], created["id"], "failed", seen=seen)
        assert all(view["state"] != "complete" for view in seen)
        assert failed["attempts"] == 4
        assert isinstance(failed["error"], str) and failed["error"]
        stored = row(db_engine, created["id"])
        assert stored["content"] is None and stored["completed_at"] is None


def test_a_stolen_lease_fences_the_stale_workers_final_write_until_it_is_reclaimed(
    start_app, db_engine, control
):
    """US-09: the final write is fenced by lease_token; a worker that lost its lease drops its result."""
    process = start_app(DOCUMENT_LEASE_SECONDS="1", DOCUMENT_RENDER_DELAY_SECONDS="2")
    with make_app_client(process.url) as client:
        trip, _ = two_item_trip(client)
        created = request_document(client, trip["id"]).json()
        wait_state(client, trip["id"], created["id"], "in_progress", timeout=20)
        seen_at = time.monotonic()  # the claim happened at or before this, so the delay ends by seen_at + 2
        thief = uuid4()
        with db_engine.begin() as connection:
            stolen = connection.execute(
                text(
                    "UPDATE documents SET lease_token = :token, lease_until = now() + make_interval(secs => 8) "
                    "WHERE id = CAST(:id AS uuid) AND state = 'in_progress'"
                ),
                {"token": thief, "id": created["id"]},
            )
            assert stolen.rowcount == 1

        # Absence cannot be polled for: wait out the render delay (2 s) plus a margin, inside the 8 s stolen lease.
        # The token check also catches a claim that ignores a live lease: the worker would have taken it back.
        time.sleep(max(0.0, seen_at + 4.0 - time.monotonic()))
        stored = row(db_engine, created["id"])
        assert stored["state"] == "in_progress"
        assert stored["lease_token"] == thief
        assert stored["content"] is None and stored["completed_at"] is None
        assert stored["attempts"] == 1
        assert get_view(client, trip["id"], created["id"])["state"] == "in_progress"

        done = wait_state(client, trip["id"], created["id"], "complete", timeout=40)
        assert done["attempts"] == 2
        assert row(db_engine, created["id"])["lease_token"] is None
        assert hashlib.sha256(download(client, trip["id"], created["id"]).content).hexdigest() == done["sha256"]


# --- 7: failure and retry ----------------------------------------------------------------------


def test_an_unrenderable_source_fails_after_three_attempts_and_retry_only_applies_to_failed(
    start_app, db_engine, control
):
    """US-09: bounded attempts end in failed with a reason, never complete; retry is for failed only."""
    idle = start_app(DOCUMENT_WORKER_ENABLED="0")
    with make_app_client(idle.url) as client:
        trip, _ = two_item_trip(client)
        bad_id = insert_document(db_engine, trip["id"])
        waiting = get_view(client, trip["id"], bad_id)
        assert waiting["state"] == "waiting"
        refused = client.post(path(trip["id"], bad_id, "/retry"))
        assert assert_error(refused, 409, "NOTHING_TO_RETRY")["details"]["current"] == waiting
        busy_token = uuid4()
        busy_id = insert_document(
            db_engine,
            trip["id"],
            source='{"n": 1}',
            state="in_progress",
            attempts=1,
            lease_token=busy_token,
            lease_until=datetime.now(timezone.utc) + timedelta(hours=1),  # live: no worker may take it
        )
        busy = get_view(client, trip["id"], busy_id)
        assert busy["state"] == "in_progress"
        refused = client.post(path(trip["id"], busy_id, "/retry"))
        assert assert_error(refused, 409, "NOTHING_TO_RETRY")["details"]["current"] == busy
        assert row(db_engine, busy_id)["state"] == "in_progress"

        worker = start_app()
        with make_app_client(worker.url) as live:
            seen = []
            failed = wait_state(live, trip["id"], bad_id, "failed", seen=seen)
            assert all(view["state"] != "complete" for view in seen)
            assert failed["attempts"] == 3
            assert isinstance(failed["error"], str) and failed["error"]
            assert ABSENT_WHEN_NOT_COMPLETE.isdisjoint(failed)
            assert "retry" in failed["nextStep"].lower()
            assert row(db_engine, bad_id)["content"] is None
            untouched = row(db_engine, busy_id)  # a live lease is not reclaimed, however long the worker ran
            assert untouched["state"] == "in_progress" and untouched["attempts"] == 1
            assert untouched["lease_token"] == busy_token
            not_ready = live.get(path(trip["id"], bad_id, "/content"))
            assert assert_error(not_ready, 409, "DOCUMENT_NOT_READY")["details"]["current"] == failed

            good = complete_document(live, trip["id"])
        worker.kill()

        retried = client.post(path(trip["id"], bad_id, "/retry"))  # the idle app: nobody can claim it yet
        assert retried.status_code == 200
        assert retried.json()["state"] == "waiting" and retried.json()["attempts"] == 0
        assert "error" not in retried.json()

        done_retry = client.post(path(trip["id"], good["id"], "/retry"))
        assert assert_error(done_retry, 409, "NOTHING_TO_RETRY")["details"]["current"] == good

    again = start_app()
    with make_app_client(again.url) as live:
        refailed = wait_state(live, trip["id"], bad_id, "failed")
        assert refailed["attempts"] == 3 and refailed["error"]


# --- 8: stored file against its record ---------------------------------------------------------


def test_a_corrupted_file_is_never_sent_marks_the_document_failed_and_retry_restores_it(
    start_app, db_engine, control
):
    """US-10: every download re-hashes; mismatch is 409 DOCUMENT_CORRUPT, failed, and retry regenerates."""
    process = start_app()
    with make_app_client(process.url) as client:
        trip, _ = two_item_trip(client)
        original = complete_document(client, trip["id"])
        original_bytes = download(client, trip["id"], original["id"]).content

        assert_constraint(
            db_engine,
            "UPDATE documents SET content = :content WHERE id = CAST(:id AS uuid)",
            {"content": b"tampered", "id": original["id"]},
            "ck_documents_hash",
        )
        assert_constraint(
            db_engine,
            "UPDATE documents SET content = NULL, content_sha256 = NULL WHERE id = CAST(:id AS uuid)",
            {"id": original["id"]},
            "ck_documents_complete",
        )
        assert download(client, trip["id"], original["id"]).content == original_bytes

        try:
            with db_engine.begin() as connection:
                connection.execute(text("ALTER TABLE documents DROP CONSTRAINT ck_documents_hash"))
                connection.execute(
                    text("UPDATE documents SET content = content || :extra WHERE id = CAST(:id AS uuid)"),
                    {"extra": b"X", "id": original["id"]},
                )
            corrupt = client.get(path(trip["id"], original["id"], "/content"))
            body = assert_error(corrupt, 409, "DOCUMENT_CORRUPT")
            assert b"<!doctype" not in corrupt.content.lower()
            assert body["details"]["current"]["state"] == "failed"
            assert body["details"]["current"]["error"]
            assert ABSENT_WHEN_NOT_COMPLETE.isdisjoint(body["details"]["current"])
            stored = row(db_engine, original["id"])
            assert stored["state"] == "failed"
            assert stored["content"] is None and stored["content_sha256"] is None
            assert stored["completed_at"] is None
        finally:
            with db_engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM documents WHERE content IS NOT NULL "
                        "AND content_sha256 <> encode(sha256(content), 'hex')"
                    )
                )
                if not connection.scalar(
                    text("SELECT count(*) FROM pg_constraint WHERE conname = 'ck_documents_hash'")
                ):
                    connection.execute(
                        text(
                            "ALTER TABLE documents ADD CONSTRAINT ck_documents_hash CHECK "
                            "(content IS NULL OR content_sha256 = encode(sha256(content), 'hex'))"
                        )
                    )

        assert client.post(path(trip["id"], original["id"], "/retry")).status_code == 200
        restored = wait_state(client, trip["id"], original["id"], "complete")
        assert restored["sha256"] == original["sha256"]
        assert download(client, trip["id"], original["id"]).content == original_bytes


# --- 9: permissions ----------------------------------------------------------------------------


def test_viewer_reads_but_cannot_write_outsider_and_bad_ids_get_the_missing_trip_404(
    start_app, control
):
    """US-02, US-10: 403 for viewer writes, 404 for non-participants, one 404 body for every bad id."""

    def respond(request):
        if request["method"] == "POST":
            return StubResponse(200, stub_search_result())
        return StubResponse(200, stub_offer(request["path"].rsplit("/", 1)[1]))

    with ProviderStub(respond) as stub:
        process = start_app(PROVIDER_BASE_URL=stub.url)
        with make_app_client(process.url) as owner:
            trip = create_planning_trip(owner)
            other_trip = create_planning_trip(owner)
            add_participant(owner, trip["id"], "ben", "viewer")
            add(owner, trip["id"], 1, "setup-offer")
            document = complete_document(owner, trip["id"])
            original = download(owner, trip["id"], document["id"]).content
            missing_trip = without_request_id(owner.get(path(str(uuid4()))))

            stub.requests.clear()
            with make_app_client(process.url, "ben") as viewer:
                assert_error(request_document(viewer, trip["id"]), 403, "FORBIDDEN")
                assert_error(viewer.post(path(trip["id"], document["id"], "/retry")), 403, "FORBIDDEN")
                listed = viewer.get(path(trip["id"]))
                assert listed.status_code == 200
                assert [entry["id"] for entry in listed.json()["documents"]] == [document["id"]]
                assert stub.requests == []  # listing never calls the provider
                assert get_view(viewer, trip["id"], document["id"]) == document
                assert download(viewer, trip["id"], document["id"]).content == original
            assert owner.get(path(trip["id"])).json()["documents"][0]["state"] == "complete"

            with make_app_client(process.url, "cam") as outsider:
                routes = [
                    outsider.post(path(trip["id"])),
                    outsider.get(path(trip["id"])),
                    outsider.get(path(trip["id"], document["id"])),
                    outsider.get(path(trip["id"], document["id"], "/content")),
                    outsider.post(path(trip["id"], document["id"], "/retry")),
                ]
                assert len(routes) == 5
                for response in routes:
                    assert without_request_id(response) == missing_trip

            for bad_id, trip_id in (
                ("not-a-uuid", trip["id"]),
                ("00000000-0000-0000-0000-000000000000", trip["id"]),
                (document["id"], other_trip["id"]),
                ("{" + document["id"] + "}", trip["id"]),  # braces: UUID() parses it, the canonical form rejects it
                (document["id"].replace("-", "").upper(), trip["id"]),  # a real id, written another way
            ):
                for response in (
                    owner.get(path(trip_id, bad_id)),
                    owner.get(path(trip_id, bad_id, "/content")),
                    owner.post(path(trip_id, bad_id, "/retry")),
                ):
                    assert without_request_id(response) == missing_trip


# --- 10: constraints ---------------------------------------------------------------------------

EXPECTED_COLUMNS = {
    "id": ("uuid", "NO"),
    "trip_id": ("uuid", "NO"),
    "trip_version": ("integer", "NO"),
    "source": ("jsonb", "NO"),
    "source_sha256": ("text", "NO"),
    "state": ("text", "NO"),
    "requested_by": ("text", "NO"),
    "requested_at": ("timestamp with time zone", "NO"),
    "attempts": ("integer", "NO"),
    "lease_token": ("uuid", "YES"),
    "lease_until": ("timestamp with time zone", "YES"),
    "content": ("bytea", "YES"),
    "content_sha256": ("text", "YES"),
    "completed_at": ("timestamp with time zone", "YES"),
    "error": ("text", "YES"),
}


def test_the_database_enforces_the_documents_invariants_by_name(empty_database):
    """US-10: columns and the five named constraints hold with direct SQL; the controls insert good rows.

    Migration parity (upgrade on an empty database, `alembic check`) is already enforced at head
    by test_app_database.test_alembic_upgrade_and_check_on_a_fresh_database, which now covers 0004.
    """
    engine = empty_database
    trip_id, other_trip_id = uuid4(), uuid4()
    insert_trip(engine, trip_id)
    insert_trip(engine, other_trip_id)
    now = datetime.now(timezone.utc)
    content = b"<!doctype html>"
    digest = hashlib.sha256(content).hexdigest()

    with engine.connect() as connection:
        columns = {
            name: (kind, nullable)
            for name, kind, nullable in connection.execute(
                text(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'documents'"
                )
            )
        }
        constraints = set(
            connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'documents'::regclass")
            ).scalars()
        )
    assert columns == EXPECTED_COLUMNS
    assert {
        "uq_documents_source",
        "ck_documents_state",
        "ck_documents_complete",
        "ck_documents_hash",
        "ck_documents_lease",
    } <= constraints

    def params(**overrides):
        return document_params(trip_id=trip_id, **overrides)

    # Controls: each kind of valid row is accepted, so the refusals below are about the named rule.
    good = {
        "complete": params(source="1", state="complete", content=content, content_sha256=digest, completed_at=now),
        "leased": params(source="2", state="in_progress", lease_token=uuid4(), lease_until=now, attempts=1),
        "failed": params(source="3", state="failed"),
    }
    with engine.begin() as connection:
        for valid in good.values():
            connection.execute(text(INSERT_DOCUMENT), valid)
        defaults = connection.execute(
            text("SELECT attempts, requested_at FROM documents WHERE id = :id"), {"id": good["failed"]["id"]}
        ).one()
        assert defaults.attempts == 0 and defaults.requested_at is not None

    def refused(constraint, **overrides):
        assert_constraint(engine, INSERT_DOCUMENT, params(source=json.dumps(uuid4().hex), **overrides), constraint)

    refused("ck_documents_state", state="bogus")
    refused("ck_documents_complete", state="complete")  # complete without content
    refused("ck_documents_complete", state="complete", content=content, content_sha256=digest)  # no completed_at
    refused("ck_documents_complete", content=content, content_sha256=digest, completed_at=now)  # waiting with a file
    # Each conjunct of ck_documents_complete alone: a complete row missing only one of the three is refused.
    refused("ck_documents_complete", state="complete", content_sha256=digest, completed_at=now)  # no content
    refused("ck_documents_complete", state="complete", content=content, completed_at=now)  # no sha
    refused("ck_documents_hash", state="complete", content=content, content_sha256="0" * 64, completed_at=now)
    refused("ck_documents_lease", state="in_progress")
    refused("ck_documents_lease", state="in_progress", lease_token=uuid4())
    assert_constraint(engine, INSERT_DOCUMENT, params(source="1"), "uq_documents_source")
    with engine.begin() as connection:  # the same source on another trip is a different document
        connection.execute(text(INSERT_DOCUMENT), document_params(trip_id=other_trip_id, source="1"))
    with pytest.raises(IntegrityError) as dangling:
        with engine.begin() as connection:
            connection.execute(text(INSERT_DOCUMENT), document_params(trip_id=uuid4(), source="5"))
    assert dangling.value.orig.sqlstate == "23503"  # foreign key to trips
