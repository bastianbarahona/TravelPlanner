# PRD: travel planning platform

> See the [candidate guide](candidate-guide.md) for evaluation, submission, and scheduling.

## Product goal

Help a group plan a trip, compare travel options, and make simulated bookings while keeping the itinerary, costs, and booking status understandable.

Travel data changes, providers fail, and several people may edit at once. The product must distinguish estimates, confirmed outcomes, and uncertainty rather than hide those differences.

## Users

- **Organizer:** creates a trip, manages its participants, and can edit it.
- **Editor:** can update the itinerary, request bookings, and generate documents.
- **Viewer:** can inspect the trip, use read-only assistant capabilities, and download its available documents.

Editors inherit viewer capabilities; organizers inherit both. Stories are grouped by role below, with shared read-only capabilities listed under Viewers. Access is limited to trips a user belongs to. You may use test identities and simplify how participants are added.

## Scope

The required product includes shared trips, provider search, itinerary editing, costs, simulated bookings, downloadable trip documents, and a read-only assistant with MCP tools.

All budgets, offers, bookings, and documents use USD. Currency conversion is out of scope. Users edit itineraries and request bookings through the application; the assistant only reads and recommends.

The [OpenAPI contract](openAPI.json) represents an external travel provider with multiple logical suppliers. It covers offers and bookings. The mock and application must communicate through that contract.

You may limit destinations, sample data, and travel rules if the required behavior remains demonstrable. Document those limits.

## User stories and acceptance criteria

### Organizers

#### US-01 — Create and return to a trip

**As an organizer, I want to define a trip so that the group can plan around shared dates and a budget.**

- A trip records one or more destinations, dates, and a budget in USD.
- The organizer can return to the trip in another session and find its saved information.
- Trips and their associated information survive an application restart.
- Invalid or incomplete inputs produce actionable feedback rather than an apparently successful save.

#### US-02 — Share a trip with appropriate access

**As an organizer, I want to add viewers and editors so that participants can contribute without all having the same permissions.**

- An organizer can give another participant view or edit access.
- Editors can perform planning actions; viewers cannot change the trip, request bookings, or start document generation.
- Authorized participants can read trip information and download its available documents.
- Nonparticipants cannot access the trip or its documents.
- Permissions apply to direct application requests, status subscriptions, and assistant tools—not just visible controls.

### Editors

#### US-04 — Build an itinerary

**As an editor, I want to add, remove, and arrange options so that the group has a shared plan.**

- Selected options appear in the itinerary with their schedules and costs.
- The application identifies missing information, expired prices, and relevant schedule conflicts, such as overlapping activities.
- Saved itinerary changes remain available when the trip is reopened.
- The application explains any simplified scheduling or travel rules.

The interface and itinerary representation are up to you. An interactive map or drag-and-drop editor is not required.

#### US-06 — Edit without silently losing changes

**As an editor, I want my changes protected when someone else edits the same trip.**

- Concurrent edits do not silently discard previously saved changes.
- Each participant can tell whether their change was saved.
- A participant affected by a conflict can understand what happened and resolve it.
- Conflict handling preserves permissions and the consistency of the trip.

Live presence and real-time collaborative editing are optional. Preventing lost changes is required.

#### US-07 — Confirm current terms before booking

**As an editor, I want to review current price and availability before committing to a booking.**

- Booking requires an explicit user action.
- Price and availability are checked before a booking is confirmed.
- A changed price or option requires the user's confirmation of the new terms.
- An unavailable option cannot be presented as successfully booked.
- A booking retains the accepted offer and price information.

#### US-08 — Recover from uncertain and partial bookings

**As an editor, I want to know what happened to each booking even when a provider fails or a response is lost.**

- Bookings distinguish pending, confirmed, failed, and locally unknown outcomes.
- Retrying the same booking intent does not create duplicate bookings within the provider's guarantees.
- The application can reconcile an unknown outcome using the provider contract.
- Users can distinguish a failed request from an unresolved booking outcome.
- If one selection is booked and another fails, each outcome remains visible and the application explains available next steps.
- Interrupted operations do not erase already confirmed bookings or silently turn uncertainty into success.

Real payments, automatic rollback across suppliers, cancellation, and refunds are out of scope.

#### US-09 — Generate a trip document

**As an editor, I want a downloadable trip summary without having to stop using the application while it is prepared.**

- The document contains the itinerary, costs, and booking statuses.
- The user can see whether generation is waiting, in progress, complete, or failed.
- Reloading or reconnecting restores the operation's current state.
- Interruptions or repeated requests do not leave a missing or incomplete file presented as ready.
- There is a way to recover or retry a failed generation.

You choose the document format and how generation and status updates work.

### Viewers

#### US-03 — Search and compare travel options

**As a participant, I want to compare transportation, accommodation, and activities so that I can choose options that fit the trip.**

- Search uses the supplied provider contract and shows price, currency, schedule, availability, and relevant restrictions.
- Users can distinguish a complete search, partial results, an empty result, and a failed search.
- Successful results remain useful when another supplier fails.
- Estimates and search offers are not presented as confirmed bookings or guaranteed prices.
- The result presentation makes its freshness clear.

#### US-05 — Understand the budget

**As a participant, I want itemized costs and a clear total so that I can compare the plan with the budget.**

- Costs match the selected options and distinguish estimates from confirmed booking prices.
- Itemized costs, totals, and budget comparisons use USD.
- Rounding rules are documented and produce reproducible results.
- Confirmed booking prices remain understandable after provider prices change.

Under the provider contract, each offer price covers the whole returned option for the specified travelers. It is not a per-person or per-night price.

#### US-10 — Access the right document

**As a participant, I want a document that accurately represents the trip information used to generate it.**

- A document identifies the trip information and point in time it represents.
- Later itinerary edits do not silently alter an existing document.
- Authorized participants can download completed documents; nonparticipants cannot.
- The application can detect or recover from mismatches between stored files and their references.
- Interrupted operations do not leave documents inaccessible or falsely reported as ready.

#### US-11 — Ask for grounded travel recommendations

**As a participant, I want an assistant to find options and explain recommendations based on my travel needs.**

- The assistant can inspect the authorized trip and search options through read-only MCP tools.
- Recommendations use retrieved information rather than invented prices, availability, or bookings.
- The assistant explains how proposed options relate to dates, budget, preferences, and restrictions, and identifies gaps or conflicts.
- If no suitable option is found, it says so.
- Tool failures and insufficient information are made clear rather than presented as successful results.
- The assistant is read-only for all roles: it can recommend changes but cannot modify the itinerary or request bookings.

#### US-12 — Use authorized trip information through MCP

**As a participant, I want the assistant to use the application's permitted trip information so that its advice reflects the plan I can actually access.**

- The application exposes read-only capabilities through Model Context Protocol (MCP), including option search and access to trip information.
- The built-in assistant discovers and invokes those capabilities through MCP.
- Tool access is tied to the acting user's permissions; a trip identifier alone does not grant access.
- The MCP layer does not expose itinerary modification or booking actions.
- The choice of MCP SDK, transport, and deployment arrangement is up to you. Supporting additional external MCP clients is not required.

## Cross-cutting behavior

These requirements apply across the stories:

- Saved state survives normal restarts; records do not imply success before the corresponding operation succeeds.
- Slow, unavailable, or rate-limited providers do not leave the interface waiting indefinitely without an understandable status.
- Long-running operations and partial failures can be diagnosed and recovered within documented limits.
- Historical booking and document information remains meaningful when external data changes.
- The application can recover its view of state after disconnections without depending on every notification arriving.

The implementation mechanisms are not prescribed. Explain the guarantees you provide and the limits you accept.

## Out of scope and optional extensions

Not required:

- Real payments, commercial reservations, cancellation, or refunds.
- A production identity or invitation system.
- Currency conversion, every destination, transportation type, or travel rule.
- A public cloud deployment.
- Interactive maps, live presence, or real-time collaborative editing.

You may add extensions, but they do not replace the required stories. Document omissions and assumptions so they can be discussed during review.
