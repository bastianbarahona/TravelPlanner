"""searchOffers and getOffer. Test numbers refer to provider-mock/README.md."""
import json
import time
from pathlib import Path

import pytest

from contract import instant
from support import APP_DIR, SUPPLIER_DEADLINE_SECONDS, current_offer, search_body

LISBON_HOTELS = ["offer-hotel-001", "offer-hotel-002", "offer-hotel-003", "offer-hotel-004", "offer-hotel-005"]


def search(client, **overrides):
    response = client.post("/offers/search", json=search_body(**overrides))
    assert response.status_code == 200
    return response.json()


def offer_ids(result):
    return [offer["id"] for offer in result["offers"]]


# Test 9: the four kinds of search result.


def test_complete_search(provider):
    result = search(provider)
    assert offer_ids(result) == LISBON_HOTELS
    assert result["partial"] is False
    assert result["warnings"] == []


@pytest.mark.parametrize(
    "destination, code, surviving",
    [
        ("Porto", "PROVIDER_UNAVAILABLE", "offer-porto-hotel-001"),
        ("Faro", "PROVIDER_TIMEOUT", "offer-faro-hotel-001"),
        ("Braga", "RATE_LIMITED", "offer-braga-hotel-001"),
    ],
)
def test_partial_search_keeps_surviving_offers(provider, destination, code, surviving):
    result = search(provider, destination=destination)
    assert offer_ids(result) == [surviving]
    assert result["partial"] is True
    assert [(w["providerId"], w["code"]) for w in result["warnings"]] == [("supplier-b", code)]


def test_partial_search_with_no_surviving_offers_is_not_an_empty_search(provider):
    result = search(provider, destination="Coimbra")
    assert result["offers"] == []
    assert result["partial"] is True


def test_empty_search(provider):
    result = search(provider, destination="Atlantis")
    assert result["offers"] == []
    assert result["partial"] is False
    assert result["warnings"] == []


@pytest.mark.parametrize("destination", ["Evora", "Sintra"])
def test_search_where_every_supplier_fails_is_503(provider, destination):
    response = provider.post("/offers/search", json=search_body(destination=destination))
    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_single_supplier_failure_is_503(provider):
    response = provider.post("/offers/search", json=search_body(destination="Porto", providerId="supplier-b"))
    assert response.status_code == 503


def test_single_supplier_rate_limited_is_429(provider):
    response = provider.post("/offers/search", json=search_body(destination="Braga", providerId="supplier-b"))
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"


def test_supplier_without_the_failed_category_keeps_the_search_complete(provider):
    result = search(provider, destination="Porto", category="activity")
    assert offer_ids(result) == ["offer-porto-activity-001"]
    assert result["partial"] is False


def test_suppliers_are_queried_concurrently_within_one_deadline(provider):
    started = time.monotonic()
    provider.post("/offers/search", json=search_body(destination="Sintra"))
    elapsed = time.monotonic() - started
    assert SUPPLIER_DEADLINE_SECONDS <= elapsed < 2 * SUPPLIER_DEADLINE_SECONDS  # sequential would be 3x


# Which offers a search returns.


def test_provider_id_limits_the_search_to_that_supplier(provider):
    assert offer_ids(search(provider, providerId="supplier-a")) == ["offer-hotel-001"]


def test_unknown_supplier_is_400(provider):
    response = provider.post("/offers/search", json=search_body(providerId="supplier-z"))
    assert response.status_code == 400
    assert response.json()["code"] == "BAD_REQUEST"


def test_known_supplier_without_the_category_is_a_complete_empty_search(provider):
    result = search(provider, category="activity", providerId="supplier-b")
    assert result["offers"] == []
    assert result["partial"] is False


@pytest.mark.parametrize("destination", ["lisbon", "LISBON", "LiSbOn"])
def test_destination_matches_case_insensitively(provider, destination):
    assert offer_ids(search(provider, destination=destination)) == LISBON_HOTELS


@pytest.mark.parametrize("destination", ["Lisb", "Lisbon ", "Lisbon, Portugal"])
def test_destination_match_is_exact(provider, destination):
    assert search(provider, destination=destination)["offers"] == []


def test_transport_matches_origin_case_insensitively(provider):
    result = search(provider, category="transport", origin="lOnDoN")
    assert offer_ids(result) == ["offer-transport-001"]


def test_transport_from_another_origin_does_not_match(provider):
    assert search(provider, category="transport", origin="Paris")["offers"] == []


def test_origin_is_ignored_for_other_categories(provider):
    assert offer_ids(search(provider, origin="Paris")) == LISBON_HOTELS


def test_party_size_must_match_exactly(provider):
    assert offer_ids(search(provider, travelers=4)) == ["offer-hotel-006"]
    assert search(provider, travelers=3)["offers"] == []


@pytest.mark.parametrize(
    "starts_on, ends_on, included",
    [
        pytest.param("2030-06-10", "2030-06-13", True, id="exact window, both ends inclusive"),
        pytest.param("2030-06-11", "2030-06-13", False, id="starts before the window"),
        pytest.param("2030-06-10", "2030-06-12", False, id="ends after the window"),
    ],
)
def test_offer_must_fit_the_inclusive_window(provider, starts_on, ends_on, included):
    ids = offer_ids(search(provider, startsOn=starts_on, endsOn=ends_on))
    assert ("offer-hotel-001" in ids) is included


def test_window_uses_the_literal_local_date_not_the_utc_date(provider):
    # Starts 2030-06-10T00:30+01:00, which is still June 9 in UTC.
    assert "offer-activity-003" in offer_ids(
        search(provider, category="activity", startsOn="2030-06-10", endsOn="2030-06-10")
    )
    assert search(provider, category="activity", startsOn="2030-06-09", endsOn="2030-06-09")["offers"] == []


def test_search_shows_unavailable_offers_as_unavailable(provider):
    by_id = {offer["id"]: offer for offer in search(provider)["offers"]}
    assert by_id["offer-hotel-005"]["available"] is False
    assert by_id["offer-hotel-001"]["available"] is True


def test_absent_restrictions_are_omitted_not_null(provider):
    by_id = {offer["id"]: offer for offer in search(provider)["offers"]}
    assert "restrictions" not in by_id["offer-hotel-003"]


def test_searched_at_is_provider_time(provider, control):
    control.pin_clock("2030-01-15T12:00:00+00:00")
    assert instant(search(provider)["searchedAt"]) == instant("2030-01-15T12:00:00Z")


def test_search_refreshes_an_expired_quote(provider, control):
    quote = current_offer(provider)
    control.pin_clock(quote["validUntil"])
    refreshed = search(provider)["offers"][0]
    assert refreshed["revision"] != quote["revision"]
    assert instant(refreshed["validUntil"]) > instant(quote["validUntil"])


def test_search_keeps_the_revision_of_an_unchanged_quote(provider):
    assert search(provider)["offers"][0]["revision"] == search(provider)["offers"][0]["revision"]


# getOffer.


def test_every_fixture_offer_satisfies_the_contract(provider):
    # Reads all of them, not only those other tests use. The client hook checks each response.
    fixtures = json.loads((Path(APP_DIR) / "fixtures.json").read_text(encoding="utf-8"))
    for offer in fixtures["offers"]:
        current_offer(provider, offer["id"])


def test_get_offer_returns_the_search_terms(provider):
    assert current_offer(provider) == search(provider)["offers"][0]


def test_get_unknown_offer_is_404(provider):
    response = provider.get("/offers/offer-does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_get_known_unavailable_offer_is_200_with_available_false(provider):
    assert current_offer(provider, "offer-hotel-005")["available"] is False


def test_unchanged_quote_keeps_its_revision(provider):
    assert current_offer(provider)["revision"] == current_offer(provider)["revision"]


def test_expired_quote_is_refreshed_with_a_new_revision_and_future_deadline(provider, control):
    quote = current_offer(provider)
    control.pin_clock(quote["validUntil"])
    refreshed = current_offer(provider)
    assert refreshed["revision"] != quote["revision"]
    assert instant(refreshed["validUntil"]) > instant(quote["validUntil"])


def test_refreshed_quote_keeps_its_price(provider, control):
    quote = current_offer(provider)
    control.pin_clock(quote["validUntil"])
    assert current_offer(provider)["price"] == quote["price"]


def test_expired_unavailable_offer_stays_unavailable_with_a_new_revision(provider, control):
    quote = current_offer(provider, "offer-hotel-005")
    control.pin_clock(quote["validUntil"])
    refreshed = current_offer(provider, "offer-hotel-005")
    assert refreshed["available"] is False
    assert refreshed["revision"] != quote["revision"]


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"price": {"amount": "500.00", "currency": "USD"}}, id="price"),
        pytest.param({"available": False}, id="availability"),
    ],
)
def test_changed_terms_produce_a_new_revision(provider, control, changes):
    before = current_offer(provider)
    control.change_offer(before["id"], **changes)
    assert current_offer(provider)["revision"] != before["revision"]
