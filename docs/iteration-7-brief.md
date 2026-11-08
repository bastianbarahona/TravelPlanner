# Iteration 7 brief: minimal frontend

The single spec for both authors. Decisions approved by the owner on 2026-11-08 at about 04:40 (UTC-3), all five as recommended. Scope: every workflow and failure state of US-01 to US-10 in a browser. The assistant panel (US-11) joins after Iteration 6 merges.

## Hard limits

- **`app/`, `contracts/`, `provider-mock/` and `tests/` are not touched.** If the UI seems to need an `app/` change, stop and report it: the Director tells the owner.
- No migration, no mutation id. Checks live under `web/`.
- No visual polish, no UI library, no router, no state library (`CLAUDE.md`, Scope guardrails).
- The UI never invents an outcome. A state, a `nextStep`, an error message and a `requestId` are shown as the server sent them.
- Money is shown as the server's string. The UI never adds, rounds or parses an amount into a number.

## Decisions

1. **Stack:** Vite, React 19, TypeScript (strict), plain `fetch`. One page. The open trip is the hash `#/trips/<tripId>`; no hash is the trip list.
2. **API access and identity:** same origin. The browser calls `/api/...`. nginx (compose) and the Vite dev server proxy `/api/` to the app with the prefix stripped, so there is no CORS and no `app/` change. An identity `<select>` holds the four compose identities (`ana`, `ben`, `cam`, `dee`, tokens `token-<name>`), stored in `sessionStorage`, default `ana`. Every request sends `Authorization: Bearer <token>`. A download is `fetch`, then a blob and an object URL, because an `<a href>` cannot send the header.
3. **Failure states:** listed in "Workflows" below.
4. **Polling:** every 3 s (`POLL_MS = 3000`, exported), `GET .../items/{itemId}/booking` for each item whose latest attempt is `pending` or `unknown`, and `GET .../documents` while any document is `waiting` or `in_progress`. It stops when nothing is open. On load, on identity change and on `window` `focus`, everything of the open trip is read again, so the view recovers after a disconnection without relying on any notification.
5. **Compose and tests:** a `web` service, built in `node:24-alpine` and served by `nginx:1.27-alpine` on `${WEB_PORT:-8080}`, after `app` is healthy. Vitest with Testing Library under `web/tests/`, and Playwright under `web/e2e/` against the compose stack with the mock's control interface on. CI gets a `web` job: `npm ci`, `tsc --noEmit`, `vitest run`, `vite build`. The e2e stays a manual heavy job, like the mutation check.

## API the UI uses

Read the routers in `app/api/` and the views in `app/services/` and `app/domain/` for the exact shapes. Every error body is `{code, message, requestId, details?}`. A 422 has `details.errors: [{field, message}]`.

| Action | Request | Who |
|---|---|---|
| List trips | `GET /trips` → `{trips: [...]}` | any identity |
| Create trip | `POST /trips` `{destinations, startsOn, endsOn, budgetUsd, travelers}` → 201 trip | any identity |
| Read trip | `GET /trips/{id}` → trip with `version`, `role` (the caller's), `participants` | member |
| Edit trip | `PATCH /trips/{id}` `{version, ...fields}` | organizer |
| Participants | `POST /trips/{id}/participants` `{userId, role}`, `PATCH .../{userId}` `{role}`, `DELETE .../{userId}` | organizer |
| Search | `POST /trips/{id}/search` `{category, origin?}` → `{legs: [{destination, status, options, warnings, searchedAt?, error?}]}` | member |
| Itinerary | `GET /trips/{id}/itinerary` → `{tripId, version, items, issues, rules, budget}` | member |
| Add, remove, reorder | `POST .../itinerary/items` `{version, offerId}`, `DELETE .../items/{itemId}?version=N`, `PUT .../order` `{version, itemIds}` | editor |
| Refresh terms | `POST .../items/{itemId}/refresh` `{version}` | editor |
| Book | `POST .../items/{itemId}/booking` `{expectedRevision, expectedPriceUsd}` → 201 attempt view, whatever the state | editor |
| Retry unknown | `POST .../items/{itemId}/booking/retry` `{}` | editor |
| Booking state | `GET .../items/{itemId}/booking` (never calls the provider) | member |
| Documents | `POST /trips/{id}/documents` (202 or 200), `GET .../documents` → `{documents}`, `GET .../{docId}/content`, `POST .../{docId}/retry` | request and retry: editor; read: member |

Check the exact delete query parameter and every field name in `app/api/` before coding against it.

## Workflows and what each state must show

Text in quotes is required visible text, so tests can find it. `data-testid` and `data-*` attributes below are the test contract: both authors use exactly these.

**Identity.** `<select aria-label="Identity">` with options `ana`, `ben`, `cam`, `dee`. Changing it reloads the open view.

**Errors, everywhere.** Any non-2xx response, a network failure or a timeout shows a `role="alert"` element with the server's `message` and its `requestId` ("Request id: ..."). A 422 also shows each `details.errors` entry next to its field as `data-testid="field-error-<field>"`. A network failure says "Could not reach the server" and is never shown as a success. A 403 says the server's message; the UI also hides write controls for roles that lack them (from the trip's `role`), but the server stays the authority.

**Trips (US-01).** `data-testid="trip-list"` with one link per trip. A "New trip" form with labels "Destinations" (comma-separated), "Start date", "End date", "Budget (USD)", "Travelers", and a "Create trip" button. On 201 the UI opens the new trip. On 422 nothing looks saved.

**Trip view.** `data-testid="trip-summary"` shows destinations, dates, budget, travelers, `version` ("Version N") and the caller's role ("Your role: organizer"). The organizer gets an edit form with the same labels and "Save trip", sending the `version` it last read.

**Stale edit, 409 (US-06).** On `VERSION_CONFLICT`, or any 409 with `details.current`, show `data-testid="conflict"` with role `alert`, containing "Not saved" and the server's message. Replace the shown state with `details.current` (a trip for `PATCH /trips`, an itinerary for itinerary writes), so the next write sends the current `version`. The user's unsaved form input stays in the form, so they can apply it again. A success shows "Saved" (`data-testid="saved"`).

**Participants (US-02).** Organizer only: a list `data-testid="participants"` with each user's role, a role `<select>` and a "Remove" button per non-organizer, and an "Add participant" form (user `<select>` labelled "User", role `<select>` labelled "Role").

**Search (US-03).** "Category" `<select>` (`transport`, `accommodation`, `activity`), optional "Origin" input, "Search" button. While waiting, "Searching..." is shown, and the button is disabled. Each leg is `data-testid="leg-<destination>"` with `data-status="<status>"` and visible text:

| Status | Visible text |
|---|---|
| `complete` | "Complete" |
| `partial` | "Partial results: some suppliers did not answer", plus each warning |
| `empty` | "No options found" |
| `failed` | "Search failed", plus `error.message`, and "Try again in N s" when `error.retryAfterSeconds` is present |

A non-failed leg shows "Searched at <searchedAt>". A partial leg with no options shows the partial text, never "No options found". Each option is `data-testid="option-<offerId>"` with title, schedule, price and currency, availability, restrictions, and the label "Estimate, not a confirmed price". Editors get "Add to itinerary". An option already in the itinerary is shown as such and its button is disabled.

**Itinerary (US-04, US-05).** Each item is `data-testid="item-<itemId>"` with `data-offer-id`, title, category, schedule, price and its kind: "Estimate" or "Confirmed price". Editors get "Move up", "Move down", "Remove" and "Book". `data-testid="issues"` lists each issue's `message`. `data-testid="rules"` lists each simplified rule. `data-testid="budget"` shows every line, `estimatedUsd`, `confirmedUsd`, `totalUsd`, `budgetUsd`, `differenceUsd` with "Over budget" or "Within budget", and `roundingRule`. A remove refused with `ITEM_HAS_BOOKING` shows its message.

**Booking (US-07).** "Book" never books. It first calls refresh, then shows `data-testid="booking-review"` with the current price, revision and availability, and "Confirm booking" and "Cancel". If refresh changed the price or the revision, it also shows "The terms changed. Review the new price before confirming." An unavailable offer shows "This option is unavailable" and no "Confirm booking". "Confirm booking" sends the reviewed `expectedRevision` and `expectedPriceUsd`. A refresh 409 is the stale-edit flow above.

**Booking states (US-08).** Each item with a `booking` shows `data-testid="booking-<itemId>"` with `data-state="<state>"`, the state's text, and the attempt's `nextStep` verbatim.

| State | Visible text | Action |
|---|---|---|
| `pending` | "Pending: the provider has not decided yet" | none; polled. After 75 s still pending, also "Still pending. You can leave this page: the booking keeps being checked." |
| `confirmed` | "Confirmed" | none |
| `failed` | "Failed: the provider refused this booking" | "Book" again (new attempt) |
| `rejected` | "Not booked: the terms were refused before booking" | "Book" again, after review |
| `unknown` | "Unknown: we could not confirm the outcome. This is not a failure." | "Retry booking" (same intent; editors) |

Items with different outcomes stay side by side. "Last checked <lastCheckedAt>" is shown when present.

**Documents (US-09, US-10).** Editors get "Generate document". The list `data-testid="documents"` has one `data-testid="document-<id>"` per document with `data-state`, "Trip version N", `requestedAt`, `attempts`, the state's text ("Waiting", "In progress", "Complete", "Failed"), and `nextStep` verbatim. "Download" only when `complete`; it downloads via blob, and a 409 `DOCUMENT_CORRUPT` is shown as an alert, never as a file. "Retry" only when `failed`, for editors. A failed document shows its `error`.

## Tests that must exist, and must be able to fail

Vitest renders `App` from `web/src/App.tsx` and stubs `globalThis.fetch` with bodies shaped like the real API (built from `app/`). The tests treat `web/src/` as a black box: they import only `App` and `POLL_MS`, and find elements by the text, labels, roles and `data-testid`s above.

1. Each search status shows its own text, and a partial leg with no options is not "No options found".
2. Search options are never labelled as confirmed.
3. A 409 on `PATCH /trips` and on an itinerary write shows "Not saved", replaces the view with `details.current`, and the next request sends the new `version`.
4. Each booking state shows its text, `nextStep`, and only its action. `unknown` shows "Retry booking" and is never "Failed".
5. Book sends refresh first, shows the review, and sends `expectedRevision` and `expectedPriceUsd` only after "Confirm booking". A changed price shows the change notice.
6. Polling: a `pending` booking is requested again after `POLL_MS` (fake timers) and stops after `confirmed`; same for a `waiting` document. A `focus` event reloads.
7. Documents: "Download" only when `complete`, "Retry" only when `failed`, and a 409 on download shows an alert.
8. Errors: a 422 shows field errors and no "Saved"; a network failure shows "Could not reach the server"; every alert shows the `requestId`.
9. A viewer sees no write control.

Playwright, against `docker compose -f docker-compose.yml -f web/e2e/compose.e2e.yml` with `MOCK_CONTROL_ENABLED=1`, on the mock fixtures (dates 2030-06-10 to 2030-06-13, 2 travelers):

1. Create a Lisbon trip, search accommodation, add an option, see it in the itinerary and the budget, reload and still see it.
2. Search Porto (partial), Coimbra (partial, no options), Evora (failed) and an unknown city (empty), and see each leg's text.
3. Stale edit: open the trip, change it through the API as the organizer, save in the UI, see "Not saved" and the new version, save again and see "Saved".
4. Book `offer-hotel-002` to `pending` then `confirmed` without a reload; `offer-hotel-004` to `failed`; `offer-hotel-005` to `rejected` or unavailable; with the mock fault at 503, a booking to `unknown`, then clear the fault and "Retry booking" to `confirmed`. All outcomes visible side by side.
5. Generate a document, see it reach "Complete" without a reload, download it.
6. A viewer (added by the organizer) sees the trip and no write control.

Every test needs a control run that fails for the stated reason, recorded by the Director.

## Roles

- Claude Sonnet (`dev-principal`): everything in `web/` except `web/tests/` and `web/e2e/*.spec.ts`, plus the `web` service in `docker-compose.yml` and the `web` job in `.github/workflows/ci.yml`.
- Codex: `web/tests/` and `web/e2e/` from this brief, without reading `web/src/`.
- Each reviews the other's artifact once. `qa-ux` reviews the UI and UX.
