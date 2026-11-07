"""createBooking and both lookups. Test numbers refer to provider-mock/README.md."""
import json
from collections import Counter
from urllib.parse import quote

import pytest

from support import (
    PENDING_DELAY_SECONDS,
    book,
    booking_body,
    current_offer,
    new_key,
    new_reference,
    post_booking,
    run_concurrently,
    shifted,
)


def by_reference(client, reference):
    return client.get(f"/bookings/by-reference/{quote(reference, safe='')}")


def assert_no_booking(client, reference):
    assert by_reference(client, reference).status_code == 404


def assert_rejected(response, code):
    assert response.status_code == 409
    assert response.json()["code"] == code


# Outcomes.


@pytest.mark.parametrize(
    "offer_id, status",
    [("offer-hotel-001", "confirmed"), ("offer-hotel-002", "pending"), ("offer-hotel-004", "failed")],
)
def test_first_acceptance_is_201_and_state_comes_from_the_body(provider, offer_id, status):
    response = post_booking(provider, booking_body(current_offer(provider, offer_id)))
    assert response.status_code == 201
    assert response.json()["status"] == status


def test_booking_records_the_accepted_terms(provider):
    offer = current_offer(provider)
    body = booking_body(offer)
    booking = post_booking(provider, body).json()
    assert booking["offer"] == offer
    assert booking["clientReference"] == body["clientReference"]


def test_failed_booking_carries_failure_and_others_omit_it(provider):
    _, _, failed = book(provider, "offer-hotel-004")
    _, _, confirmed = book(provider, "offer-hotel-001")
    _, _, pending = book(provider, "offer-hotel-002")
    assert set(failed["failure"]) == {"code", "message"}
    assert "failure" not in confirmed
    assert "failure" not in pending


@pytest.mark.parametrize(
    "offer_id, outcome", [("offer-hotel-002", "confirmed"), ("offer-hotel-003", "failed")]
)
def test_pending_resolves_only_after_the_delay(provider, control, offer_id, outcome):
    _, body, booking = book(provider, offer_id)
    due = shifted(booking["createdAt"], seconds=PENDING_DELAY_SECONDS)

    control.pin_clock(shifted(due, microseconds=-1))
    assert by_reference(provider, body["clientReference"]).json()["status"] == "pending"

    control.pin_clock(due)
    resolved = by_reference(provider, body["clientReference"]).json()
    assert resolved["status"] == outcome
    assert ("failure" in resolved) == (outcome == "failed")
    assert resolved["updatedAt"] > resolved["createdAt"]


def test_resolving_changes_only_the_outcome_fields(provider, control):
    _, body, booking = book(provider, "offer-hotel-003")
    control.pin_clock(shifted(booking["createdAt"], seconds=PENDING_DELAY_SECONDS))
    resolved = by_reference(provider, body["clientReference"]).json()
    for field in ("id", "clientReference", "offer", "createdAt"):
        assert resolved[field] == booking[field]


def test_terminal_outcome_and_updated_at_do_not_change_on_later_reads(provider, control):
    _, body, booking = book(provider, "offer-hotel-002")
    control.pin_clock(shifted(booking["createdAt"], seconds=PENDING_DELAY_SECONDS))
    first = by_reference(provider, body["clientReference"]).json()
    control.pin_clock(shifted(booking["createdAt"], days=30))
    assert by_reference(provider, body["clientReference"]).json() == first


def test_terminal_outcome_does_not_reverse_if_the_clock_goes_back(provider, control):
    # Recomputing on each read would look identical until the clock steps back.
    _, body, booking = book(provider, "offer-hotel-002")
    control.pin_clock(shifted(booking["createdAt"], seconds=PENDING_DELAY_SECONDS))
    assert by_reference(provider, body["clientReference"]).json()["status"] == "confirmed"

    control.pin_clock(booking["createdAt"])
    assert by_reference(provider, body["clientReference"]).json()["status"] == "confirmed"


def test_both_lookups_return_the_same_state(provider):
    _, body, booking = book(provider)
    assert provider.get(f"/bookings/{booking['id']}").json() == booking
    assert by_reference(provider, body["clientReference"]).json() == booking


def test_lookup_of_missing_records_is_404(provider):
    assert provider.get("/bookings/booking-does-not-exist").status_code == 404
    assert by_reference(provider, "ref-does-not-exist").status_code == 404


def test_reference_that_is_not_url_safe_can_be_looked_up(provider):
    reference = "trip 42/hotel?attempt=1&é"
    offer = current_offer(provider)
    booking = post_booking(provider, booking_body(offer, clientReference=reference)).json()
    found = by_reference(provider, reference)
    assert found.status_code == 200
    assert found.json()["id"] == booking["id"]


# Test 3: replay returns 200 with the current state, whatever happened to the quote.


def test_replay_returns_200_with_the_same_booking(provider):
    key, body, booking = book(provider)
    replay = post_booking(provider, body, key)
    assert replay.status_code == 200
    assert replay.json() == booking


def test_json_property_order_does_not_make_a_body_different(provider):
    key, body, booking = book(provider)
    shuffled = dict(reversed(body.items())) | {"expectedPrice": dict(reversed(body["expectedPrice"].items()))}
    assert json.dumps(shuffled) != json.dumps(body)
    replay = post_booking(provider, shuffled, key)
    assert replay.status_code == 200
    assert replay.json()["id"] == booking["id"]


def test_replay_succeeds_after_the_quote_expires(provider, control):
    key, body, booking = book(provider)
    control.pin_clock(booking["offer"]["validUntil"])
    replay = post_booking(provider, body, key)
    assert replay.status_code == 200
    assert replay.json() == booking


def test_replay_succeeds_after_the_price_changes_and_keeps_the_accepted_price(provider, control):
    key, body, booking = book(provider)
    control.change_offer(booking["offer"]["id"], price={"amount": "999.00", "currency": "USD"})
    replay = post_booking(provider, body, key)
    assert replay.status_code == 200
    assert replay.json()["offer"]["price"] == booking["offer"]["price"]


def test_replay_succeeds_after_the_offer_becomes_unavailable(provider, control):
    key, body, booking = book(provider)
    control.change_offer(booking["offer"]["id"], available=False)
    assert post_booking(provider, body, key).status_code == 200


def test_replay_returns_the_current_state_not_the_first_response(provider, control):
    key, body, booking = book(provider, "offer-hotel-002")
    assert booking["status"] == "pending"
    control.pin_clock(shifted(booking["createdAt"], seconds=PENDING_DELAY_SECONDS))
    replay = post_booking(provider, body, key)
    assert replay.status_code == 200
    assert replay.json()["status"] == "confirmed"


def test_failed_booking_replays_as_failed_and_is_never_replaced(provider):
    key, body, booking = book(provider, "offer-hotel-004")
    replay = post_booking(provider, body, key)
    assert replay.status_code == 200
    assert replay.json() == booking


def test_accepted_snapshot_survives_later_offer_changes(provider, control):
    _, body, booking = book(provider)
    control.change_offer(booking["offer"]["id"], price={"amount": "999.00", "currency": "USD"})
    assert by_reference(provider, body["clientReference"]).json()["offer"] == booking["offer"]


# Test 4: concurrency.


def test_concurrent_identical_requests_create_exactly_one_booking(provider):
    body, key = booking_body(current_offer(provider)), new_key()
    responses = run_concurrently([lambda: post_booking(provider, body, key)] * 24)
    assert Counter(r.status_code for r in responses) == {201: 1, 200: 23}
    assert len({r.json()["id"] for r in responses}) == 1


def test_concurrent_new_keys_on_one_reference_create_exactly_one_booking(provider):
    body = booking_body(current_offer(provider))
    responses = run_concurrently([lambda: post_booking(provider, body)] * 24)
    assert Counter(r.status_code for r in responses) == {201: 1, 409: 23}
    assert {r.json()["code"] for r in responses if r.status_code == 409} == {"REFERENCE_CONFLICT"}


# Test 5: the four 409 cases, in the order of CONTRACT_NOTES.md 2.1.


def test_same_key_with_a_different_body_is_idempotency_conflict(provider):
    key, body, booking = book(provider)
    response = post_booking(provider, body | {"clientReference": new_reference()}, key)
    assert_rejected(response, "IDEMPOTENCY_CONFLICT")
    assert by_reference(provider, body["clientReference"]).json() == booking


def test_new_key_with_a_used_reference_is_reference_conflict(provider):
    _, body, booking = book(provider)
    assert_rejected(post_booking(provider, body), "REFERENCE_CONFLICT")
    assert by_reference(provider, body["clientReference"]).json() == booking


def test_failed_booking_spends_its_reference(provider):
    _, body, _ = book(provider, "offer-hotel-004")
    assert_rejected(post_booking(provider, body), "REFERENCE_CONFLICT")


def test_unavailable_offer_is_offer_unavailable(provider):
    body = booking_body(current_offer(provider, "offer-hotel-005"))
    assert_rejected(post_booking(provider, body), "OFFER_UNAVAILABLE")
    assert_no_booking(provider, body["clientReference"])


@pytest.mark.parametrize(
    "stale",
    [
        pytest.param({"expectedOfferRevision": "quote-old"}, id="revision"),
        pytest.param({"expectedPrice": {"amount": "419.99", "currency": "USD"}}, id="price"),
    ],
)
def test_mismatched_terms_are_offer_changed(provider, stale):
    body = booking_body(current_offer(provider), **stale)
    assert_rejected(post_booking(provider, body), "OFFER_CHANGED")
    assert_no_booking(provider, body["clientReference"])


def test_unknown_offer_is_404_and_creates_nothing(provider):
    offer = current_offer(provider) | {"id": "offer-does-not-exist"}
    body = booking_body(offer)
    response = post_booking(provider, body)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert_no_booking(provider, body["clientReference"])


def test_key_is_resolved_before_the_reference_and_the_offer(provider, control):
    # Step 4 before steps 5 to 8: this body also has a used reference and a dead offer.
    key, body, _ = book(provider)
    other = book(provider)[1]
    control.change_offer(body["offerId"], available=False)
    response = post_booking(provider, body | {"clientReference": other["clientReference"]}, key)
    assert_rejected(response, "IDEMPOTENCY_CONFLICT")


def test_reference_conflict_wins_over_an_unknown_offer(provider):
    # Step 5 before step 6 (A3).
    _, body, _ = book(provider)
    assert_rejected(post_booking(provider, body | {"offerId": "offer-does-not-exist"}), "REFERENCE_CONFLICT")


def test_reference_conflict_wins_over_an_unavailable_offer(provider, control):
    # Step 5 before step 7 (A3).
    _, body, _ = book(provider)
    control.change_offer(body["offerId"], available=False)
    assert_rejected(post_booking(provider, body), "REFERENCE_CONFLICT")


def test_reference_conflict_wins_over_changed_terms(provider):
    # Step 5 before step 8 (A3).
    _, body, _ = book(provider)
    assert_rejected(post_booking(provider, body | {"expectedOfferRevision": "quote-old"}), "REFERENCE_CONFLICT")


def test_offer_unavailable_wins_over_a_stale_revision(provider, control):
    # Step 7 before step 8 (A4): making an offer unavailable also changes its revision.
    offer = current_offer(provider)
    control.change_offer(offer["id"], available=False)
    assert_rejected(post_booking(provider, booking_body(offer)), "OFFER_UNAVAILABLE")


def test_offer_unavailable_wins_over_an_expired_quote(provider, control):
    # Step 7 before step 8 (A4).
    offer = current_offer(provider, "offer-hotel-005")
    control.pin_clock(offer["validUntil"])
    assert_rejected(post_booking(provider, booking_body(offer)), "OFFER_UNAVAILABLE")


def test_rejected_key_is_not_consumed(provider, control):
    # A6: an identical body under a rejected key is evaluated again.
    offer = current_offer(provider)
    control.change_offer(offer["id"], available=False)
    current = current_offer(provider)
    body, key = booking_body(current), new_key()
    assert_rejected(post_booking(provider, body, key), "OFFER_UNAVAILABLE")

    fresh = control.change_offer(offer["id"], available=True)
    retry = booking_body(fresh, clientReference=body["clientReference"])
    assert post_booking(provider, retry, key).status_code == 201


def test_rejected_reference_is_not_consumed(provider):
    # A7: a rejected request leaves no record, so its reference stays free.
    offer = current_offer(provider)
    reference = new_reference()
    stale = booking_body(offer, clientReference=reference, expectedOfferRevision="quote-old")
    assert_rejected(post_booking(provider, stale), "OFFER_CHANGED")
    assert post_booking(provider, booking_body(offer, clientReference=reference)).status_code == 201


# Test 7: callers are isolated from each other.


def test_callers_cannot_see_each_others_bookings(provider, other_caller):
    _, body, booking = book(provider)
    assert other_caller.get(f"/bookings/{booking['id']}").status_code == 404
    assert by_reference(other_caller, body["clientReference"]).status_code == 404


def test_callers_can_reuse_the_same_reference(provider, other_caller):
    _, body, booking = book(provider)
    response = post_booking(other_caller, body)
    assert response.status_code == 201
    assert response.json()["id"] != booking["id"]
    assert by_reference(provider, body["clientReference"]).json()["id"] == booking["id"]
    assert by_reference(other_caller, body["clientReference"]).json()["id"] == response.json()["id"]


def test_idempotency_keys_are_scoped_to_the_caller(provider, other_caller):
    key, body, booking = book(provider)
    response = post_booking(other_caller, body, key)
    assert response.status_code == 201
    assert response.json()["id"] != booking["id"]


# Test 8: quote expiry at the boundary. validUntil is exclusive (:951).


def test_acceptance_just_before_valid_until_is_accepted(provider, control):
    offer = current_offer(provider)
    control.pin_clock(shifted(offer["validUntil"], microseconds=-1))
    assert post_booking(provider, booking_body(offer)).status_code == 201


def test_acceptance_exactly_at_valid_until_is_rejected(provider, control):
    offer = current_offer(provider)
    control.pin_clock(offer["validUntil"])
    body = booking_body(offer)
    assert_rejected(post_booking(provider, body), "OFFER_CHANGED")
    assert_no_booking(provider, body["clientReference"])


def test_acceptance_after_valid_until_is_rejected(provider, control):
    offer = current_offer(provider)
    control.pin_clock(shifted(offer["validUntil"], seconds=1))
    assert_rejected(post_booking(provider, booking_body(offer)), "OFFER_CHANGED")


def test_expired_quote_can_be_booked_at_its_refreshed_terms(provider, control):
    offer = current_offer(provider)
    control.pin_clock(offer["validUntil"])
    refreshed = current_offer(provider)
    assert post_booking(provider, booking_body(refreshed)).status_code == 201
