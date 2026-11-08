# Demo

A walkthrough of the main workflow and of the failure and concurrency scenarios. Each scenario gives numbered steps that reproduce from the mock's fixtures, the exact outcome to expect, and the test that proves it. Fixtures: dates `2030-06-10` to `2030-06-13`, 2 travelers (`provider-mock/README.md`).

Commands are bash. Browser steps use the identity selector at `http://localhost:8080`.

## Start the stack

Scenarios S4, S5 and S7 change the provider's behavior through its control interface, which is off by default. It is outside the contract, and the application never calls it.

```bash
MOCK_CONTROL_ENABLED=1 docker compose up --build
```

In PowerShell, copy `.env.example` to `.env`, set `MOCK_CONTROL_ENABLED=1` there, and run `docker compose up --build`.

Between scenarios, reset the provider (it clears bookings, keys, references, the clock and any fault):

```bash
curl -X POST localhost:4010/__control/reset
```

Every API call below sends an identity, for example `-H "Authorization: Bearer token-ana"`. Identities: `token-ana`, `token-ben`, `token-cam`, `token-dee`, as the users `ana`, `ben`, `cam`, `dee`.

## S1 Main workflow (browser)

1. As `ana`, create a trip: Destinations `Lisbon`, Start date `2030-06-10`, End date `2030-06-13`, Budget (USD) `2000.00`, Travelers `2`, then Create trip. The URL becomes `#/trips/<tripId>`, and the trip summary shows Lisbon.
2. In Participants, add `ben` as `editor` and `cam` as `viewer` (Add participant).
3. In Search, pick Category `accommodation` and press Search. The Lisbon leg shows `Complete` with its options. Each option says "Estimate, not a confirmed price".
4. Add `offer-hotel-001` ("Three-night central hotel stay") with Add to itinerary. Search `activity` and add `offer-activity-002` ("Historic tram tour").
5. Read the Itinerary, Issues and Budget sections. The budget shows `Estimated (USD)` with the sum of the two items (`420.00` for the hotel plus `45.00`) and `Within budget`. Items show "Estimate".
6. On the hotel, press Book. The review panel shows the terms the server holds. Press Confirm booking. The item shows `Confirmed`, its price kind becomes "Confirmed price", and the budget moves that line from `Estimated (USD)` to `Confirmed (USD)`.
7. In Documents, press Generate document. Wait for `Complete` (no reload), then Download. The file is `trip-<...>.html`.
8. Switch the Identity selector to `cam`. The summary says "Your role: viewer". No Save trip, Add participant, Move up, Move down, Remove, Book, Retry booking, Generate document or Add to itinerary button appears. Search still works.
9. Run `docker compose restart app` and reload the page. The trip, itinerary, confirmed booking and document are all still there: state lives in Postgres.

Proved by pieces, not by one test. In `web/e2e/travelplanner.spec.ts`: "creates a Lisbon trip, adds accommodation, and preserves the itinerary" proves step 1, the search and add of steps 3 and 4 (hotel only), and that they survive a reload; "generates a document to completion without reload and downloads it" proves step 7, on its own trip; "shows a viewer the trip without any write control" proves step 8. Participants (step 2) are covered by `tests/test_app_permissions.py`, booking (step 6) by the S4 and S5 tests, and the budget lines of steps 5 and 6 by `tests/test_app_bookings.py`. Steps 2 and 4 to 6 are not one e2e. The restart in step 9 is a manual check: no automated test restarts the application container.

## S2 Partial and failed searches

1. Reset the provider. As `ana`, create a trip with destinations `Porto, Coimbra, Evora, Atlantis` (same dates, 2 travelers).
2. Search `accommodation`.
3. Expect one leg per destination, each distinct:
   - Porto: `partial`, "Partial results: some suppliers did not answer" (the options from the supplier that answered are listed).
   - Coimbra: `partial`, the same text, with no options, and never "No options found".
   - Evora: "Search failed".
   - Atlantis (any city not in the fixtures): "No options found".
4. A failure in one leg never loses the others. Faro (deadline), Braga (rate limited) and Sintra (failed) behave the same way, by their fixture.

Proved by: e2e "keeps Porto, Coimbra, Evora, and an unknown city's search outcomes distinct".

## S3 Stale edit, 409

1. Create a trip as `ana`, keep its id and read its `version` `V`:

   ```bash
   TRIP=$(curl -s -X POST localhost:8000/trips -H "Authorization: Bearer token-ana" -H "Content-Type: application/json" \
     -d '{"destinations":["Lisbon"],"startsOn":"2030-06-10","endsOn":"2030-06-13","budgetUsd":"2000.00","travelers":2}' \
     | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
   V=$(curl -s localhost:8000/trips/$TRIP -H "Authorization: Bearer token-ana" | python -c "import sys,json;print(json.load(sys.stdin)['version'])")
   ```

2. Send two PATCHes with the same version:

   ```bash
   curl -s -X PATCH localhost:8000/trips/$TRIP -H "Authorization: Bearer token-ana" -H "Content-Type: application/json" -d '{"version":'"$V"',"budgetUsd":"1900.00"}'
   curl -s -X PATCH localhost:8000/trips/$TRIP -H "Authorization: Bearer token-ana" -H "Content-Type: application/json" -d '{"version":'"$V"',"budgetUsd":"1850.00"}'
   ```

3. The first answers 200 with `version` V+1. The second answers 409 with code `VERSION_CONFLICT`, the message "The trip changed since you read it. Review details.current and send its version.", and `details.current` holding the current trip (budget `1900.00`).
4. In the browser, on a new trip (repeat step 1 and skip step 2): open `http://localhost:8080/#/trips/$TRIP` first, so the page loads version `V`. Then send only the first PATCH of step 2 by curl. Then edit the budget in the page to `1850.00` and press Save trip. The page shows "Not saved", the same message and a `Request id: request-...`. The summary now shows the server's version, and the field keeps your typed `1850.00`.
5. Press Save trip again. It saves ("Saved"). That PATCH sent the version and only the field you typed, so it cannot revert a field you never touched.

Proved by: e2e "recovers a stale trip edit and succeeds on the second save", and `tests/test_app_concurrency.py` (`test_trip_patch_makes_every_itinerary_write_stale`, `test_itinerary_write_makes_trip_patch_stale`).

## S4 Unknown booking, then retry

1. Reset the provider and create a fresh Lisbon trip (as in S1, steps 1 to 4, with `offer-hotel-001` only).
2. Press Book on the hotel to open the review panel.
3. Make the provider fail: `curl -X POST localhost:4010/__control/fault -H "Content-Type: application/json" -d '{"status":503}'`.
4. Press Confirm booking. The item shows state `unknown` with "Unknown: we could not confirm the outcome. This is not a failure." A 503 is never proof that the provider rejected it.
5. Clear the fault: `curl -X POST localhost:4010/__control/fault -H "Content-Type: application/json" -d '{"status":null}'`.
6. Press Retry booking. The item becomes `Confirmed`. The retry resent the stored idempotency key and the same request, so the provider holds one booking, with the same `clientReference` (`trip-<tripId>-item-<itemId>-attempt-1`).

Proved by: e2e "shows pending, confirmed, failed, unavailable, unknown, and retried booking outcomes side by side", and `tests/test_app_booking_recovery.py::test_crash_while_post_is_unanswered_keeps_unknown_and_retry_resends_same_key_and_body`.

## S5 Pending, failed and unavailable side by side

No user action is needed after Confirm: the UI reads each open booking every 3 seconds, and the server's loop reconciles with the provider (5, 10, 30, then 60 seconds).

1. Reset the provider. Make `offer-hotel-005` addable, since the fixture marks it unavailable: `curl -X POST localhost:4010/__control/offers/offer-hotel-005 -H "Content-Type: application/json" -d '{"available":true}'`.
2. Create a Lisbon trip, search `accommodation`, and add `offer-hotel-002`, `offer-hotel-003`, `offer-hotel-004`, `offer-hotel-005` and `offer-hotel-001`.
3. Book `offer-hotel-002` (Book, then Confirm booking). It shows `pending`, "Pending: the provider has not decided yet". After about 5 seconds (`MOCK_PENDING_DELAY_SECONDS`) it becomes `Confirmed` by itself.
4. Book `offer-hotel-003`. It shows `pending`, then `failed`, "Failed: the provider refused this booking".
5. Book `offer-hotel-004`. It shows `failed` at once, with the same text (US-08: a failed booking is shown as such, beside the others).
6. Make `offer-hotel-005` unavailable again: `curl -X POST localhost:4010/__control/offers/offer-hotel-005 -H "Content-Type: application/json" -d '{"available":false}'`, then press Book on it. The review says "This option is unavailable" and offers no Confirm booking button. The Issues section reads "Sold-out boutique hotel is no longer available."
7. The four items keep their own states: confirmed, failed, unavailable, and one `unknown` if you also run S4 on `offer-hotel-001`.

Proved by: e2e "shows pending, confirmed, failed, unavailable, unknown, and retried booking outcomes side by side" (it covers `offer-hotel-002`, `-004`, `-005` and `-001`), and `tests/test_app_booking_recovery.py::test_pending_booking_reaches_its_outcome_with_no_user_action`. `offer-hotel-003` (pending, then failed) is a fixture behavior of the mock. No e2e step books it, so it is a manual check here.

## S6 Document generation and immutability

1. Reset the provider, create a Lisbon trip, add `offer-hotel-001`, and press Generate document.
2. The document appears as `Waiting` or `In progress`, then becomes `Complete` without a reload. It shows "Trip version N". Download it.
3. Edit the trip (Save trip with a new budget). The existing document is unchanged: it still says its old "Trip version" and downloads the same bytes.
4. Press Generate document again. A new document appears with the new Trip version. Pressing it again without changing the trip returns the existing one (the API answers 200 instead of 202), because the same state hashes to the same source.

Proved by: e2e "generates a document to completion without reload and downloads it", and `tests/test_app_documents.py` (snapshot, single document per state and retry behavior).

## S7 Assistant

The assistant answers from the application's MCP tools only. It is read-only, and every participant may ask.

1. Reset the provider. As `ana`, create a Lisbon trip and add `ben` as `viewer`:

   ```bash
   TRIP=$(curl -s -X POST localhost:8000/trips -H "Authorization: Bearer token-ana" -H "Content-Type: application/json" \
     -d '{"destinations":["Lisbon"],"startsOn":"2030-06-10","endsOn":"2030-06-13","budgetUsd":"2000.00","travelers":2}' \
     | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
   curl -s -X POST localhost:8000/trips/$TRIP/participants -H "Authorization: Bearer token-ana" -H "Content-Type: application/json" -d '{"userId":"ben","role":"viewer"}'
   ```

2. E1, ask as `ben`:

   ```bash
   curl -s -X POST localhost:8000/trips/$TRIP/assistant -H "Authorization: Bearer token-ben" -H "Content-Type: application/json" \
     -d '{"question":"Please recommend a hotel for Lisbon"}'
   ```

   Expect `{tripId, mode: "deterministic", answer, toolCalls}`. The answer recommends up to three hotels with offer ids and prices that the tools returned, and `toolCalls` lists `get_itinerary`, `get_trip` and `search_options` (measured as `cam`, a viewer).
3. E3: `curl -X POST localhost:4010/__control/fault -H "Content-Type: application/json" -d '{"status":503}'`, then ask the same question. The answer says "Search for accommodation in Lisbon failed; no options from it." and invents no option. `search_options` is not an error: a failed leg is a result. Clear the fault with `{"status":null}`.
4. Reproduce all seven fixed cases deterministically, with the clock pinned by the script:

   ```bash
   .venv/Scripts/python tests/assistant_evals.py
   ```

   E1 to E7 must score 100% (no model, no key). `ANTHROPIC_API_KEY=... .venv/Scripts/python tests/assistant_evals.py --mode anthropic` runs them against Claude and reports scores (billed).

In the browser: open the trip as `ben`, type the question in the panel's Question field (`data-testid="assistant"`) and press Ask. The button reads "Asking..." and is disabled while it waits. The answer shows as plain text lines, with the mode and each tool call by name, a failed call marked "(tool error)". A 503 `ASSISTANT_UNAVAILABLE` shows in the page's alert with its `requestId`. Proved by `web/tests/assistant.test.tsx` and its e2e case, (`docs/STATUS.md` 2.15).

Proved by: `tests/assistant_evals.py` and `tests/test_assistant_evals.py` (E1 to E7, with a control per check), and `tests/test_app_assistant.py`.
