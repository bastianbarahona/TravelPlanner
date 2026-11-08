import "@testing-library/jest-dom/vitest";

import type { ComponentType } from "react";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import * as AppModule from "../src/App";
import {
  DOCUMENT_ID,
  ITEM_ID,
  TRIP_ID,
  apiError,
  booking,
  document,
  fetchRouter,
  item,
  itinerary,
  jsonResponse,
  offer,
  option,
  trip,
  type RecordedRequest,
  type Route,
} from "./fixtures";

const appExports = AppModule as unknown as {
  App?: ComponentType;
  default?: ComponentType;
  POLL_MS: number;
};
const App = (appExports.App ?? appExports.default) as ComponentType;
const POLL_MS = appExports.POLL_MS;

function openTripRoutes(
  tripView = trip(),
  itineraryView = itinerary(),
  documents: unknown[] = [],
): Record<string, Route> {
  return {
    "GET /api/trips": jsonResponse({ trips: [tripView] }),
    [`GET /api/trips/${TRIP_ID}`]: jsonResponse(tripView),
    [`GET /api/trips/${TRIP_ID}/itinerary`]: jsonResponse(itineraryView),
    [`GET /api/trips/${TRIP_ID}/documents`]: jsonResponse({ documents }),
  };
}

function renderOpen(routes: Record<string, Route>) {
  window.location.hash = `#/trips/${TRIP_ID}`;
  const router = fetchRouter(routes);
  vi.stubGlobal("fetch", router.fetch);
  const rendered = render(<App />);
  return { ...rendered, router };
}

function expectRequest(
  request: RecordedRequest | undefined,
  method: string,
  path: string,
  body?: unknown,
  token = "token-ana",
) {
  expect(request).toMatchObject({ method, path, authorization: `Bearer ${token}` });
  if (arguments.length >= 4) expect(request?.body).toEqual(body);
}

function expectAuthorized(requests: RecordedRequest[], tokens = ["token-ana"]) {
  for (const request of requests) {
    expect(tokens.map((token) => `Bearer ${token}`)).toContain(request.authorization);
  }
}

// Response.json() needs more microtask turns than any fixed count of `await Promise.resolve()`; only fake timers
// advanced asynchronously drain it. Call this under vi.useFakeTimers().
async function flushEffects() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

// jsdom has no object URLs and does not navigate: stub both, so a download can be observed.
function stubObjectUrls() {
  const create = vi.fn((_blob: Blob) => "blob:trip-document");
  const revoke = vi.fn();
  Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  return { create, revoke };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
  window.location.hash = "";
});

// Vitest item 1: catches collapsed search statuses, missing warnings/retry text, and partial-empty legs mislabeled as empty.
test("renders complete, partial, partial-without-options, failed, and empty search legs distinctly", async () => {
  const user = userEvent.setup();
  const searchBody = {
    legs: [
      {
        destination: "Lisbon",
        status: "complete",
        options: [option()],
        warnings: [],
        searchedAt: "2030-06-01T12:00:00Z",
      },
      {
        destination: "Porto",
        status: "partial",
        options: [option({ offer: { id: "offer-porto", destination: "Porto" } })],
        warnings: [
          { providerId: "supplier-b", code: "PROVIDER_TIMEOUT", message: "supplier-b did not answer" },
        ],
        searchedAt: "2030-06-01T12:01:00Z",
      },
      {
        destination: "Coimbra",
        status: "partial",
        options: [],
        warnings: [
          { providerId: "supplier-b", code: "PROVIDER_UNAVAILABLE", message: "supplier-b is unavailable" },
        ],
        searchedAt: "2030-06-01T12:02:00Z",
      },
      {
        destination: "Evora",
        status: "failed",
        options: [],
        warnings: [],
        searchedAt: "2030-06-01T12:02:30Z", // present on purpose: a failed leg must still not show "Searched at"
        error: {
          code: "PROVIDER_UNAVAILABLE",
          message: "Every supplier is unavailable.",
          retryAfterSeconds: 12,
        },
      },
      {
        destination: "Atlantis",
        status: "empty",
        options: [],
        warnings: [],
        searchedAt: "2030-06-01T12:03:00Z",
      },
    ],
  };
  const { router } = renderOpen({
    ...openTripRoutes(
      trip({ destinations: ["Lisbon", "Porto", "Coimbra", "Evora", "Atlantis"] }),
    ),
    [`POST /api/trips/${TRIP_ID}/search`]: jsonResponse(searchBody),
  });

  await screen.findByTestId("trip-summary");
  await user.selectOptions(screen.getByLabelText("Category"), "accommodation");
  await user.click(screen.getByRole("button", { name: "Search" }));

  expect(within(await screen.findByTestId("leg-Lisbon")).getByText("Complete")).toBeVisible();
  const porto = screen.getByTestId("leg-Porto");
  expect(porto).toHaveAttribute("data-status", "partial");
  expect(within(porto).getByText("Partial results: some suppliers did not answer")).toBeVisible();
  expect(within(porto).getByText("supplier-b did not answer")).toBeVisible();
  const coimbra = screen.getByTestId("leg-Coimbra");
  expect(within(coimbra).getByText("Partial results: some suppliers did not answer")).toBeVisible();
  expect(within(coimbra).getByText("supplier-b is unavailable")).toBeVisible();
  expect(within(coimbra).queryByText("No options found")).not.toBeInTheDocument();
  const evora = screen.getByTestId("leg-Evora");
  expect(within(evora).getByText("Search failed")).toBeVisible();
  expect(within(evora).getByText("Every supplier is unavailable.")).toBeVisible();
  expect(within(evora).getByText("Try again in 12 s")).toBeVisible();
  for (const leg of [screen.getByTestId("leg-Lisbon"), porto, coimbra, screen.getByTestId("leg-Atlantis")]) {
    expect(within(leg).getByText(/Searched at/)).toBeVisible();
  }
  expect(within(evora).queryByText(/Searched at/)).not.toBeInTheDocument();
  expect(within(screen.getByTestId("leg-Atlantis")).getByText("No options found")).toBeVisible();

  const request = router.requests.find((entry) => entry.path.endsWith("/search"));
  expectRequest(request, "POST", `/api/trips/${TRIP_ID}/search`, { category: "accommodation" });
  expectAuthorized(router.requests);
});

// Vitest item 2: catches search quotes presented as booked or confirmed prices.
test("labels every search option as an estimate and renders the provider terms", async () => {
  const user = userEvent.setup();
  const { router } = renderOpen({
    ...openTripRoutes(),
    [`POST /api/trips/${TRIP_ID}/search`]: jsonResponse({
      legs: [
        {
          destination: "Lisbon",
          status: "complete",
          options: [option()],
          warnings: [],
          searchedAt: "2030-06-01T12:00:00Z",
        },
      ],
    }),
  });

  await screen.findByTestId("trip-summary");
  await user.selectOptions(screen.getByLabelText("Category"), "accommodation");
  await user.click(screen.getByRole("button", { name: "Search" }));

  const quote = await screen.findByTestId("option-offer-hotel-001");
  expect(quote).toHaveTextContent("Three-night central hotel stay");
  expect(quote).toHaveTextContent("2030-06-10T15:00:00+01:00");
  expect(quote).toHaveTextContent("2030-06-13T11:00:00+01:00");
  expect(quote).toHaveTextContent("420.00");
  expect(quote).toHaveTextContent("USD");
  expect(quote).toHaveTextContent("Available");
  expect(quote).toHaveTextContent("Non-refundable simulated booking");
  expect(within(quote).getByText("Estimate, not a confirmed price")).toBeVisible();
  expect(within(quote).queryByText("Confirmed price")).not.toBeInTheDocument();
  // The only place the word appears is the negation inside the estimate label.
  expect((quote.textContent ?? "").match(/confirm/gi)).toHaveLength(1);

  expectRequest(
    router.requests.find((entry) => entry.path.endsWith("/search")),
    "POST",
    `/api/trips/${TRIP_ID}/search`,
    { category: "accommodation" },
  );
  expectAuthorized(router.requests);
});

// Vitest item 3: catches stale trip/itinerary state, discarded form edits, and retries that resend the obsolete version.
test("adopts current state after trip and itinerary conflicts before retrying with the new version", async () => {
  const user = userEvent.setup();
  let patchCount = 0;
  const currentTrip = trip({ version: 7, budgetUsd: "900.00" });
  const savedTrip = trip({ version: 8, budgetUsd: "850.00" });
  const patchRun = renderOpen({
    ...openTripRoutes(),
    [`PATCH /api/trips/${TRIP_ID}`]: () => {
      patchCount += 1;
      return patchCount === 1
        ? apiError(
            409,
            "VERSION_CONFLICT",
            "The trip changed since you read it. Review details.current and send its version.",
            "request-trip-conflict",
            { current: currentTrip },
          )
        : jsonResponse(savedTrip);
    },
  });

  await screen.findByTestId("trip-summary");
  const budgetInput = screen.getByLabelText("Budget (USD)");
  await user.clear(budgetInput);
  await user.type(budgetInput, "850.00");
  await user.click(screen.getByRole("button", { name: "Save trip" }));

  const conflict = await screen.findByTestId("conflict");
  expect(conflict).toHaveAttribute("role", "alert");
  expect(conflict).toHaveTextContent("Not saved");
  expect(conflict).toHaveTextContent("The trip changed since you read it.");
  expect(conflict).toHaveTextContent("Request id: request-trip-conflict");
  expect(screen.getByTestId("trip-summary")).toHaveTextContent("Version 7");
  expect((budgetInput as HTMLInputElement).value).toBe("850.00");
  await user.click(screen.getByRole("button", { name: "Save trip" }));
  expect(await screen.findByTestId("saved")).toHaveTextContent("Saved");

  const patchRequests = patchRun.router.requests.filter((entry) => entry.method === "PATCH");
  expectRequest(patchRequests[0], "PATCH", `/api/trips/${TRIP_ID}`);
  expectRequest(patchRequests[1], "PATCH", `/api/trips/${TRIP_ID}`);
  expect(patchRequests[0].body).toEqual({ version: 1, budgetUsd: "850.00" });
  expect(patchRequests[1].body).toEqual({ version: 7, budgetUsd: "850.00" });
  expectAuthorized(patchRun.router.requests);
  patchRun.unmount();

  const concurrentId = "44444444-4444-4444-8444-444444444444";
  const addedId = "55555555-5555-4555-8555-555555555555";
  const concurrentItem = item({
    id: concurrentId,
    offer: { id: "offer-concurrent", title: "Concurrent hotel" },
  });
  const addedOffer = offer({ id: "offer-new", title: "Hotel selected by this user" });
  const currentItinerary = itinerary({ version: 6, items: [concurrentItem] });
  const savedItinerary = itinerary({
    version: 7,
    items: [concurrentItem, item({ id: addedId, position: 1, offer: addedOffer })],
  });
  let addCount = 0;
  const itineraryRun = renderOpen({
    ...openTripRoutes(),
    [`POST /api/trips/${TRIP_ID}/search`]: jsonResponse({
      legs: [
        {
          destination: "Lisbon",
          status: "complete",
          options: [option({ offer: addedOffer })],
          warnings: [],
          searchedAt: "2030-06-01T12:00:00Z",
        },
      ],
    }),
    [`POST /api/trips/${TRIP_ID}/itinerary/items`]: () => {
      addCount += 1;
      return addCount === 1
        ? apiError(
            409,
            "VERSION_CONFLICT",
            "The itinerary changed since you read it. Review details.current and send its version.",
            "request-itinerary-conflict",
            { current: currentItinerary },
          )
        : jsonResponse(savedItinerary, 201);
    },
  });

  await screen.findByTestId("trip-summary");
  await user.selectOptions(screen.getByLabelText("Category"), "accommodation");
  await user.click(screen.getByRole("button", { name: "Search" }));
  const selected = await screen.findByTestId("option-offer-new");
  await user.click(within(selected).getByRole("button", { name: "Add to itinerary" }));
  expect(await screen.findByTestId(`item-${concurrentId}`)).toHaveTextContent("Concurrent hotel");
  expect(screen.getByTestId("conflict")).toHaveTextContent("Not saved");
  expect(screen.getByTestId("conflict")).toHaveTextContent("Request id: request-itinerary-conflict");
  await user.click(within(selected).getByRole("button", { name: "Add to itinerary" }));
  expect(await screen.findByTestId(`item-${addedId}`)).toHaveTextContent("Hotel selected by this user");
  expect(await screen.findByTestId("saved")).toHaveTextContent("Saved");
  expect(screen.queryByTestId("conflict")).not.toBeInTheDocument();

  const addRequests = itineraryRun.router.requests.filter(
    (entry) => entry.method === "POST" && entry.path.endsWith("/itinerary/items"),
  );
  expectRequest(addRequests[0], "POST", `/api/trips/${TRIP_ID}/itinerary/items`, {
    version: 1,
    offerId: "offer-new",
  });
  expectRequest(addRequests[1], "POST", `/api/trips/${TRIP_ID}/itinerary/items`, {
    version: 6,
    offerId: "offer-new",
  });
  expectAuthorized(itineraryRun.router.requests);
});

// Finding 1 (US-06): after a 409 the form must not resend fields the user never typed, or it silently reverts
// another editor's saved change.
test("resends only the typed field with the new version after a 409, never a stale budget", async () => {
  const user = userEvent.setup();
  let patchCount = 0;
  const { router } = renderOpen({
    ...openTripRoutes(),
    [`PATCH /api/trips/${TRIP_ID}`]: () => {
      patchCount += 1;
      return patchCount === 1
        ? apiError(409, "VERSION_CONFLICT", "The trip changed since you read it.", "request-stale-budget", {
            current: trip({ version: 7, budgetUsd: "900.00" }),
          })
        : jsonResponse(trip({ version: 8, budgetUsd: "900.00", destinations: ["Lisbon", "Porto"] }));
    },
  });

  await screen.findByTestId("trip-summary");
  const destinations = screen.getByLabelText("Destinations");
  await user.clear(destinations);
  await user.type(destinations, "Lisbon, Porto");
  await user.click(screen.getByRole("button", { name: "Save trip" }));
  expect(await screen.findByTestId("conflict")).toHaveTextContent("Not saved");
  expect(screen.getByTestId("trip-summary")).toHaveTextContent("Version 7");
  expect(screen.getByTestId("trip-summary")).toHaveTextContent("900.00");
  expect((destinations as HTMLInputElement).value).toBe("Lisbon, Porto");

  await user.click(screen.getByRole("button", { name: "Save trip" }));
  expect(await screen.findByTestId("saved")).toHaveTextContent("Saved");

  const patches = router.requests.filter((entry) => entry.method === "PATCH");
  expect(patches).toHaveLength(2);
  expect(patches[0].body).toEqual({ version: 1, destinations: ["Lisbon", "Porto"] });
  expect(patches[1].body).toEqual({ version: 7, destinations: ["Lisbon", "Porto"] });
  expect(patches[1].body).not.toHaveProperty("budgetUsd");
});

// Vitest item 4: catches conflated booking outcomes, altered next steps, wrong actions, and unknown shown as failed.
test("shows each booking state with its verbatim next step and only the permitted action", async () => {
  const states = ["pending", "confirmed", "failed", "rejected", "unknown"];
  const stateItems = states.map((state, index) => {
    const id = `00000000-0000-4000-8000-00000000000${index}`;
    return item({
      id,
      position: index,
      offer: { id: `offer-${state}`, title: `${state} hotel` },
      priceKind: state === "confirmed" ? "confirmed" : "estimate",
      bookable: !["pending", "confirmed", "unknown"].includes(state),
      booking: booking(state, {
        id: `attempt-${state}`,
        itemId: id,
        nextStep: `Server next step: ${state}.`,
        ...(state === "pending" ? { lastCheckedAt: "2030-06-01T12:05:00+00:00" } : {}),
      }),
    });
  });
  const { router } = renderOpen(
    openTripRoutes(trip(), itinerary({ items: stateItems })),
  );

  await screen.findByTestId("trip-summary");
  const visible = {
    pending: "Pending: the provider has not decided yet",
    confirmed: "Confirmed",
    failed: "Failed: the provider refused this booking",
    rejected: "Not booked: the terms were refused before booking",
    unknown: "Unknown: we could not confirm the outcome. This is not a failure.",
  } as const;
  for (const [index, state] of states.entries()) {
    const itemView = screen.getByTestId(`item-00000000-0000-4000-8000-00000000000${index}`);
    const bookingView = within(itemView).getByTestId(`booking-00000000-0000-4000-8000-00000000000${index}`);
    expect(bookingView).toHaveAttribute("data-state", state);
    // Exact match: "Confirmed" must not be satisfied by "Confirmed price".
    expect(within(bookingView).getByText(visible[state as keyof typeof visible], { exact: true })).toBeVisible();
    if (state === "confirmed") {
      expect(within(itemView).getByText("Confirmed price", { exact: true })).toBeVisible();
      expect(within(itemView).queryByText("Estimate", { exact: true })).not.toBeInTheDocument();
    } else {
      expect(within(itemView).getByText("Estimate", { exact: true })).toBeVisible();
      expect(within(itemView).queryByText("Confirmed price", { exact: true })).not.toBeInTheDocument();
    }
    expect(bookingView).toHaveTextContent(`Server next step: ${state}.`);
    const hasBook = state === "failed" || state === "rejected";
    expect(within(itemView).queryByRole("button", { name: "Book" }) !== null).toBe(hasBook);
    expect(within(itemView).queryByRole("button", { name: "Retry booking" }) !== null).toBe(
      state === "unknown",
    );
  }
  expect(screen.getByTestId("booking-00000000-0000-4000-8000-000000000004")).not.toHaveTextContent(
    "Failed: the provider refused this booking",
  );
  expect(screen.getByTestId("booking-00000000-0000-4000-8000-000000000000")).toHaveTextContent(
    "Last checked 2030-06-01T12:05:00+00:00",
  );
  expectRequest(
    router.requests.find((entry) => entry.path === `/api/trips/${TRIP_ID}`),
    "GET",
    `/api/trips/${TRIP_ID}`,
  );
  expectAuthorized(router.requests);
});

// Vitest item 5: catches direct booking without refresh/review, stale terms, and confirmation before explicit consent.
test("refreshes before review and books only the changed terms the user confirms", async () => {
  const user = userEvent.setup();
  const oldOffer = offer({ price: { amount: "100.00", currency: "USD" }, revision: "revision-1" });
  const freshOffer = offer({ price: { amount: "125.00", currency: "USD" }, revision: "revision-2" });
  const initial = itinerary({ items: [item({ offer: oldOffer })] });
  const refreshed = itinerary({ version: 2, items: [item({ offer: freshOffer })] });
  const confirmed = booking("confirmed", {
    booking: {
      id: "provider-booking-1",
      clientReference: "reference-1",
      status: "confirmed",
      offer: freshOffer,
      createdAt: "2030-06-01T12:00:00Z",
      updatedAt: "2030-06-01T12:00:00Z",
    },
    nextStep: "This item is booked.",
  });
  // What the server returns after the booking: priceKind and the booking are computed server side (item_view).
  const booked = itinerary({
    version: 2,
    items: [item({ offer: freshOffer, priceKind: "confirmed", bookable: false, booking: confirmed })],
  });
  let stage: "initial" | "refreshed" | "booked" = "initial";
  const { router } = renderOpen({
    ...openTripRoutes(trip(), initial),
    [`GET /api/trips/${TRIP_ID}/itinerary`]: () =>
      jsonResponse({ initial, refreshed, booked }[stage]),
    [`POST /api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/refresh`]: () => {
      stage = "refreshed";
      return jsonResponse(refreshed);
    },
    [`POST /api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/booking`]: () => {
      stage = "booked";
      return jsonResponse(confirmed, 201);
    },
  });

  const shownItem = await screen.findByTestId(`item-${ITEM_ID}`);
  await user.click(within(shownItem).getByRole("button", { name: "Book" }));

  const review = await screen.findByTestId("booking-review");
  expect(review).toHaveTextContent("125.00");
  expect(review).toHaveTextContent("revision-2");
  expect(review).toHaveTextContent("The terms changed. Review the new price before confirming.");
  expect(
    router.requests.filter((entry) => entry.path.endsWith(`/items/${ITEM_ID}/booking`)),
  ).toHaveLength(0);
  const refreshRequest = router.requests.find((entry) => entry.path.endsWith(`/items/${ITEM_ID}/refresh`));
  expectRequest(
    refreshRequest,
    "POST",
    `/api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/refresh`,
    { version: 1 },
  );

  await user.click(within(review).getByRole("button", { name: "Confirm booking" }));
  const bookingRequest = router.requests.find((entry) => entry.path.endsWith(`/items/${ITEM_ID}/booking`));
  expectRequest(
    bookingRequest,
    "POST",
    `/api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/booking`,
    { expectedRevision: "revision-2", expectedPriceUsd: "125.00" },
  );
  expect(router.requests.indexOf(refreshRequest!)).toBeLessThan(router.requests.indexOf(bookingRequest!));
  const confirmedBooking = await screen.findByTestId(`booking-${ITEM_ID}`);
  expect(confirmedBooking).toHaveAttribute("data-state", "confirmed");
  expect(within(confirmedBooking).getByText("Confirmed", { exact: true })).toBeVisible();
  await within(screen.getByTestId(`item-${ITEM_ID}`)).findByText("Confirmed price", { exact: true });
  expect(screen.getByTestId(`item-${ITEM_ID}`)).toHaveTextContent("125.00");
  expect(screen.getByTestId(`item-${ITEM_ID}`)).toHaveAttribute("data-offer-id", "offer-hotel-001");
  expect(
    router.requests.filter((entry) => entry.path.endsWith(`/items/${ITEM_ID}/booking`) && entry.method === "POST"),
  ).toHaveLength(1);
  expectAuthorized(router.requests);
});

// Vitest item 5 (unchanged terms): catches a change notice shown when nothing changed.
test("shows no change notice when the refreshed terms equal the stored terms, and Cancel books nothing", async () => {
  const user = userEvent.setup();
  const same = itinerary({ items: [item()] });
  const { router } = renderOpen({
    ...openTripRoutes(trip(), same),
    [`POST /api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/refresh`]: jsonResponse(same),
  });

  const shownItem = await screen.findByTestId(`item-${ITEM_ID}`);
  await user.click(within(shownItem).getByRole("button", { name: "Book", exact: true }));
  const review = await screen.findByTestId("booking-review");
  expect(review).toHaveTextContent("420.00");
  expect(review).toHaveTextContent("revision-1");
  expect(review).toHaveTextContent("Available");
  expect(within(review).queryByText(/The terms changed/)).not.toBeInTheDocument();
  expect(within(review).getByRole("button", { name: "Confirm booking" })).toBeVisible();

  await user.click(within(review).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByTestId("booking-review")).not.toBeInTheDocument();
  expect(
    router.requests.filter((entry) => entry.method === "POST" && entry.path.endsWith(`/items/${ITEM_ID}/booking`)),
  ).toHaveLength(0);
  expect(screen.queryByTestId(`booking-${ITEM_ID}`)).not.toBeInTheDocument();
});

// Vitest item 5 (unavailable): catches an unavailable offer that can still be confirmed.
test("an unavailable refreshed offer cannot be confirmed", async () => {
  const user = userEvent.setup();
  const gone = itinerary({ version: 2, items: [item({ offer: { available: false }, bookable: false })] });
  const { router } = renderOpen({
    ...openTripRoutes(trip(), itinerary({ items: [item()] })),
    [`POST /api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/refresh`]: jsonResponse(gone),
  });

  const shownItem = await screen.findByTestId(`item-${ITEM_ID}`);
  await user.click(within(shownItem).getByRole("button", { name: "Book", exact: true }));
  const review = await screen.findByTestId("booking-review");
  expect(review).toHaveTextContent("This option is unavailable");
  expect(within(review).queryByRole("button", { name: "Confirm booking" })).not.toBeInTheDocument();
  expect(within(review).getByRole("button", { name: "Cancel" })).toBeVisible();
  expect(
    router.requests.filter((entry) => entry.method === "POST" && entry.path.endsWith(`/items/${ITEM_ID}/booking`)),
  ).toHaveLength(0);
});

// Busy state: catches a second POST from a double click while the first is still in flight.
test("Confirm booking and Create trip are disabled while their request is in flight", async () => {
  const user = userEvent.setup();
  let release!: (response: Response) => void;
  const held = new Promise<Response>((resolve) => (release = resolve));
  const fresh = itinerary({ items: [item()] });
  const { router, unmount } = renderOpen({
    ...openTripRoutes(trip(), fresh),
    [`POST /api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/refresh`]: jsonResponse(fresh),
    [`POST /api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/booking`]: () => held,
  });
  const shownItem = await screen.findByTestId(`item-${ITEM_ID}`);
  await user.click(within(shownItem).getByRole("button", { name: "Book", exact: true }));
  const confirm = within(await screen.findByTestId("booking-review")).getByRole("button", { name: "Confirm booking" });
  await user.click(confirm);
  expect(confirm).toBeDisabled();
  await user.click(confirm);
  expect(
    router.requests.filter((entry) => entry.method === "POST" && entry.path.endsWith(`/items/${ITEM_ID}/booking`)),
  ).toHaveLength(1);
  await act(async () => release(jsonResponse(booking("unknown"), 201)));
  unmount();

  window.location.hash = "";
  let releaseCreate!: (response: Response) => void;
  const heldCreate = new Promise<Response>((resolve) => (releaseCreate = resolve));
  const created = fetchRouter({
    ...openTripRoutes(),
    "GET /api/trips": jsonResponse({ trips: [] }),
    "POST /api/trips": () => heldCreate,
  });
  vi.stubGlobal("fetch", created.fetch);
  render(<App />);
  const create = await screen.findByRole("button", { name: "Create trip" });
  await user.click(create);
  expect(create).toBeDisabled();
  await user.click(create);
  expect(created.requests.filter((entry) => entry.method === "POST" && entry.path === "/api/trips")).toHaveLength(1);
  await act(async () => releaseCreate(jsonResponse(trip(), 201)));
});

// Vitest item 6: catches missing/never-ending booking and document polling and focus that fails to recover the open trip.
test("polls open bookings and documents once per interval, stops when closed, and reloads on focus", async () => {
  vi.useFakeTimers();
  const pending = booking("pending", { nextStep: "The provider is still deciding." });
  const confirmed = booking("confirmed", { nextStep: "This item is booked." });
  const pendingItinerary = itinerary({ items: [item({ booking: pending, bookable: false })] });
  const confirmedItinerary = itinerary({
    items: [item({ booking: confirmed, bookable: false, priceKind: "confirmed" })],
  });
  const waiting = document("waiting", { nextStep: "The document is queued." });
  const complete = document("complete", {
    completedAt: "2030-06-01T12:00:03.000000Z",
    sha256: "abc123",
    sizeBytes: 42,
    nextStep: "The document is ready. Download its content.",
  });
  let itineraryReads = 0;
  let documentReads = 0;
  const { router } = renderOpen({
    ...openTripRoutes(),
    [`GET /api/trips/${TRIP_ID}/itinerary`]: () => {
      itineraryReads += 1;
      return jsonResponse(itineraryReads === 1 ? pendingItinerary : confirmedItinerary);
    },
    [`GET /api/trips/${TRIP_ID}/documents`]: () => {
      documentReads += 1;
      return jsonResponse({ documents: [documentReads === 1 ? waiting : complete] });
    },
    [`GET /api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/booking`]: jsonResponse(confirmed),
  });

  await flushEffects();
  expect(screen.getByTestId(`booking-${ITEM_ID}`)).toHaveAttribute("data-state", "pending");
  expect(screen.getByTestId(`document-${DOCUMENT_ID}`)).toHaveAttribute("data-state", "waiting");
  expect(
    router.requests.filter((entry) => entry.path.endsWith(`/items/${ITEM_ID}/booking`)),
  ).toHaveLength(0);

  await act(async () => vi.advanceTimersByTimeAsync(POLL_MS));
  await flushEffects();
  expect(screen.getByTestId(`booking-${ITEM_ID}`)).toHaveAttribute("data-state", "confirmed");
  // The state change moved the budget and the price kind on the server, so the itinerary was read again (load + this).
  expect(itineraryReads).toBe(2);
  expect(within(screen.getByTestId(`item-${ITEM_ID}`)).getByText("Confirmed price", { exact: true })).toBeVisible();
  expect(screen.getByTestId(`document-${DOCUMENT_ID}`)).toHaveAttribute("data-state", "complete");
  const bookingPolls = router.requests.filter((entry) => entry.path.endsWith(`/items/${ITEM_ID}/booking`)).length;
  const documentPolls = router.requests.filter(
    (entry) => entry.path === `/api/trips/${TRIP_ID}/documents`,
  ).length;
  expect(bookingPolls).toBe(1);
  expect(documentPolls).toBe(2);
  expectRequest(
    router.requests.find((entry) => entry.path.endsWith(`/items/${ITEM_ID}/booking`)),
    "GET",
    `/api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/booking`,
  );

  await act(async () => vi.advanceTimersByTimeAsync(POLL_MS * 2));
  expect(router.requests.filter((entry) => entry.path.endsWith(`/items/${ITEM_ID}/booking`))).toHaveLength(1);
  expect(
    router.requests.filter((entry) => entry.path === `/api/trips/${TRIP_ID}/documents`),
  ).toHaveLength(2);

  const tripReadsBeforeFocus = router.requests.filter(
    (entry) => entry.path === `/api/trips/${TRIP_ID}`,
  ).length;
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(
    router.requests.filter((entry) => entry.path === `/api/trips/${TRIP_ID}`),
  ).toHaveLength(tripReadsBeforeFocus + 1);
  expect(itineraryReads).toBe(3);
  expect(documentReads).toBe(3);
  expectAuthorized(router.requests);
});

// Vitest item 7: catches document actions on the wrong states and corrupt downloads treated as successful files.
test("offers document download and retry only in their valid states and surfaces a corrupt download", async () => {
  const user = userEvent.setup();
  const documents = [
    document("waiting", { id: "30000000-0000-4000-8000-000000000001" }),
    document("in_progress", { id: "30000000-0000-4000-8000-000000000002" }),
    document("complete", {
      id: DOCUMENT_ID,
      completedAt: "2030-06-01T12:00:03.000000Z",
      sha256: "abc123",
      sizeBytes: 42,
    }),
    document("failed", {
      id: "30000000-0000-4000-8000-000000000004",
      error: "The document could not be generated. Retry to try again.",
    }),
  ];
  const corruptCurrent = document("failed", {
    id: DOCUMENT_ID,
    error: "The stored file did not match its record. Retry to generate it again.",
  });
  const urls = stubObjectUrls();
  const { router } = renderOpen({
    ...openTripRoutes(trip(), itinerary(), documents),
    [`GET /api/trips/${TRIP_ID}/documents/${DOCUMENT_ID}/content`]: apiError(
      409,
      "DOCUMENT_CORRUPT",
      "The stored file did not match its record. Retry to generate it again.",
      "request-document-corrupt",
      { current: corruptCurrent },
    ),
  });

  await screen.findByTestId("documents");
  const waiting = screen.getByTestId("document-30000000-0000-4000-8000-000000000001");
  const active = screen.getByTestId("document-30000000-0000-4000-8000-000000000002");
  const complete = screen.getByTestId(`document-${DOCUMENT_ID}`);
  const failed = screen.getByTestId("document-30000000-0000-4000-8000-000000000004");
  expect(waiting).toHaveAttribute("data-state", "waiting");
  expect(active).toHaveAttribute("data-state", "in_progress");
  expect(within(waiting).queryByRole("button", { name: /Download|Retry/ })).not.toBeInTheDocument();
  expect(within(active).queryByRole("button", { name: /Download|Retry/ })).not.toBeInTheDocument();
  expect(within(complete).getByRole("button", { name: "Download" })).toBeVisible();
  expect(within(complete).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  expect(within(failed).getByRole("button", { name: "Retry" })).toBeVisible();
  expect(within(failed).queryByRole("button", { name: "Download" })).not.toBeInTheDocument();
  expect(failed).toHaveTextContent("The document could not be generated. Retry to try again.");

  await user.click(within(complete).getByRole("button", { name: "Download" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("The stored file did not match its record. Retry to generate it again.");
  expect(alert).toHaveTextContent("Request id: request-document-corrupt");
  expect(urls.create).not.toHaveBeenCalled(); // a corrupt document is never offered as a file
  expectRequest(
    router.requests.find((entry) => entry.path.endsWith(`/${DOCUMENT_ID}/content`)),
    "GET",
    `/api/trips/${TRIP_ID}/documents/${DOCUMENT_ID}/content`,
  );
  expectAuthorized(router.requests);
});

// Vitest item 7 (success): catches a download that is not fetched with the Authorization header, or that never reaches a file.
test("downloads a complete document through an authorized fetch and a blob", async () => {
  const user = userEvent.setup();
  const urls = stubObjectUrls();
  const { router } = renderOpen({
    ...openTripRoutes(trip(), itinerary(), [document("complete", { sha256: "abc123", sizeBytes: 12 })]),
    [`GET /api/trips/${TRIP_ID}/documents/${DOCUMENT_ID}/content`]: new Response("<html></html>", {
      status: 200,
      headers: {
        "content-type": "text/html",
        "content-disposition": 'attachment; filename="trip-summary.html"',
      },
    }),
  });

  const shown = await screen.findByTestId(`document-${DOCUMENT_ID}`);
  await user.click(within(shown).getByRole("button", { name: "Download" }));
  await vi.waitFor(() => expect(urls.create).toHaveBeenCalledTimes(1));
  // jsdom and Node have distinct Blob classes, so the object is checked by what it carries.
  expect(urls.create.mock.calls[0][0]).toMatchObject({ size: "<html></html>".length, type: "text/html" });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expectRequest(
    router.requests.find((entry) => entry.path.endsWith(`/${DOCUMENT_ID}/content`)),
    "GET",
    `/api/trips/${TRIP_ID}/documents/${DOCUMENT_ID}/content`,
  );
  expectAuthorized(router.requests);
});

// Vitest item 5 (stale terms): catches a refused confirmation that drops the user without showing the new terms.
test("a TERMS_CHANGED refusal on Confirm booking refreshes again and reopens the review", async () => {
  const user = userEvent.setup();
  const fresh = itinerary({ items: [item()] });
  const { router } = renderOpen({
    ...openTripRoutes(trip(), fresh),
    [`POST /api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/refresh`]: jsonResponse(fresh),
    [`POST /api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/booking`]: apiError(
      409,
      "TERMS_CHANGED",
      "The terms you confirmed are not the ones stored for this item.",
      "request-terms-changed",
      { current: fresh.items[0] },
    ),
  });

  const shownItem = await screen.findByTestId(`item-${ITEM_ID}`);
  await user.click(within(shownItem).getByRole("button", { name: "Book", exact: true }));
  await user.click(within(await screen.findByTestId("booking-review")).getByRole("button", { name: "Confirm booking" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Request id: request-terms-changed");
  await vi.waitFor(() =>
    expect(router.requests.filter((entry) => entry.path.endsWith(`/items/${ITEM_ID}/refresh`))).toHaveLength(2),
  );
  expect(await screen.findByTestId("booking-review")).toBeVisible();
  expect(screen.queryByTestId(`booking-${ITEM_ID}`)).not.toBeInTheDocument(); // nothing was booked
});

// Vitest item 7 (repeat): catches a Generate click that appears to do nothing when the document already exists.
test("a Generate document answered 200 says the trip state already has a document", async () => {
  const user = userEvent.setup();
  const existing = document("complete", { sha256: "abc123", sizeBytes: 42 });
  renderOpen({
    ...openTripRoutes(trip(), itinerary(), [existing]),
    [`POST /api/trips/${TRIP_ID}/documents`]: jsonResponse(existing, 200),
  });

  await screen.findByTestId(`document-${DOCUMENT_ID}`);
  await user.click(screen.getByRole("button", { name: "Generate document" }));
  expect(await screen.findByTestId("saved")).toHaveTextContent("This trip state already has a document.");
});

// Vitest item 6 (release): catches a pending booking that stays silent forever, or that is released before 75 s.
test("after 75 s still pending the page says it may be left, and before 75 s it does not", async () => {
  // The attempt was created at 2030-06-01T12:00:00Z (fixtures): the clock starts 10 s later.
  vi.useFakeTimers({ now: new Date("2030-06-01T12:00:10Z") });
  const pending = booking("pending", { nextStep: "The provider is still deciding." });
  const { router } = renderOpen({
    ...openTripRoutes(trip(), itinerary({ items: [item({ booking: pending, bookable: false })] })),
    [`GET /api/trips/${TRIP_ID}/itinerary/items/${ITEM_ID}/booking`]: jsonResponse(pending),
  });
  const release = "Still pending. You can leave this page: the booking keeps being checked.";

  await flushEffects();
  expect(screen.getByTestId(`booking-${ITEM_ID}`)).toHaveAttribute("data-state", "pending");
  expect(screen.queryByText(release)).not.toBeInTheDocument();

  await act(async () => vi.advanceTimersByTimeAsync(60_000)); // 70 s since creation
  expect(
    router.requests.filter((entry) => entry.path.endsWith(`/items/${ITEM_ID}/booking`)).length,
  ).toBeGreaterThan(5); // still polled
  expect(screen.queryByText(release)).not.toBeInTheDocument();

  await act(async () => vi.advanceTimersByTimeAsync(10_000)); // 80 s since creation
  expect(screen.getByText(release)).toBeVisible();
  expect(screen.getByTestId(`booking-${ITEM_ID}`)).toHaveAttribute("data-state", "pending");
});

// Vitest item 1 (wait): catches a search that gives no sign of waiting, or lets the user send it twice.
test("shows Searching... and disables Search while a search is in flight", async () => {
  const user = userEvent.setup();
  let release!: (response: Response) => void;
  const held = new Promise<Response>((resolve) => (release = resolve));
  const { router } = renderOpen({
    ...openTripRoutes(),
    [`POST /api/trips/${TRIP_ID}/search`]: () => held,
  });

  await screen.findByTestId("trip-summary");
  await user.selectOptions(screen.getByLabelText("Category"), "accommodation");
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(screen.getByText("Searching...")).toBeVisible();
  expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(router.requests.filter((entry) => entry.path.endsWith("/search"))).toHaveLength(1);

  await act(async () =>
    release(
      jsonResponse({
        legs: [{ destination: "Lisbon", status: "complete", options: [option()], warnings: [], searchedAt: "2030-06-01T12:00:00Z" }],
      }),
    ),
  );
  expect(await screen.findByTestId("leg-Lisbon")).toBeVisible();
  expect(screen.queryByText("Searching...")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Search" })).toBeEnabled();
});

// Empty and loading states: catches a blank page that cannot be told from "nothing yet" or "still loading".
test("says so when there are no trips, items, issues or documents, and while the trip loads", async () => {
  window.location.hash = "";
  const listRun = fetchRouter({ "GET /api/trips": jsonResponse({ trips: [] }) });
  vi.stubGlobal("fetch", listRun.fetch);
  const list = render(<App />);
  expect(await screen.findByText("No trips yet")).toBeVisible();
  list.unmount();

  let release!: (response: Response) => void;
  const held = new Promise<Response>((resolve) => (release = resolve));
  const { unmount } = renderOpen({
    ...openTripRoutes(),
    [`GET /api/trips/${TRIP_ID}`]: () => held,
  });
  expect((await screen.findAllByText("Loading...")).length).toBeGreaterThan(0);
  await act(async () => release(jsonResponse(trip())));
  expect(await screen.findByTestId("trip-summary")).toBeVisible();
  expect(await screen.findByText("No items yet")).toBeVisible();
  expect(screen.getByText("No issues found")).toBeVisible();
  expect(screen.getByText("No documents yet")).toBeVisible();
  await vi.waitFor(() => expect(screen.queryByText("Loading...")).not.toBeInTheDocument());
  unmount();

  renderOpen(openTripRoutes(trip(), itinerary({ items: [item()], issues: [{ code: "X", message: "Something to fix", itemIds: [] }] }), [document("waiting")]));
  await screen.findByTestId(`item-${ITEM_ID}`);
  await screen.findByTestId(`document-${DOCUMENT_ID}`);
  expect(screen.queryByText("No items yet")).not.toBeInTheDocument();
  expect(screen.queryByText("No issues found")).not.toBeInTheDocument();
  expect(screen.queryByText("No documents yet")).not.toBeInTheDocument();
});

// Real browsers: Element.scrollIntoView() and HTMLElement.focus() return a Promise (Chromium 158) or undefined (jsdom has
// no scrollIntoView). An effect that returns that Promise makes React call it as a cleanup, which throws and unmounts the
// whole app (a blank page). jsdom alone cannot show it, so the stubs return a Promise.
test("a notice, an itinerary add and an identity switch do not blank the page when scrollIntoView and focus return a Promise", async () => {
  const user = userEvent.setup();
  const uncaught: unknown[] = [];
  const onError = (event: ErrorEvent) => {
    event.preventDefault();
    uncaught.push(event.error ?? event.message);
  };
  window.addEventListener("error", onError);
  const scrollDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView");
  const focusDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "focus");
  Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, writable: true, value: () => Promise.resolve() });
  Object.defineProperty(HTMLElement.prototype, "focus", { configurable: true, writable: true, value: () => Promise.resolve() });
  try {
    const added = item({ id: "77777777-7777-4777-8777-777777777777", offer: { id: "offer-promise", title: "Promise hotel" } });
    const { router } = renderOpen({
      ...openTripRoutes(),
      [`POST /api/trips/${TRIP_ID}/search`]: jsonResponse({
        legs: [
          {
            destination: "Lisbon",
            status: "complete",
            options: [option({ offer: { id: "offer-promise", title: "Promise hotel" } })],
            warnings: [],
            searchedAt: "2030-06-01T12:00:00Z",
          },
        ],
      }),
      [`POST /api/trips/${TRIP_ID}/itinerary/items`]: jsonResponse(itinerary({ version: 2, items: [added] }), 201),
    });

    await screen.findByTestId("trip-summary");
    await user.selectOptions(screen.getByLabelText("Category"), "accommodation");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(within(await screen.findByTestId("option-offer-promise")).getByRole("button", { name: "Add to itinerary" }));
    expect(await screen.findByTestId("saved")).toHaveTextContent("Saved");

    await user.selectOptions(screen.getByLabelText("Identity"), "ben"); // the notice is cleared: its effect cleanup runs
    await vi.waitFor(() =>
      expect(router.requests.some((entry) => entry.authorization === "Bearer token-ben")).toBe(true),
    );
    expect(screen.getByRole("heading", { name: "Travel Planner", level: 1 })).toBeVisible();
    expect(await screen.findByTestId("trip-summary")).toBeVisible();
    expect(uncaught).toEqual([]);
  } finally {
    window.removeEventListener("error", onError);
    for (const [target, name, descriptor] of [
      [Element.prototype, "scrollIntoView", scrollDescriptor],
      [HTMLElement.prototype, "focus", focusDescriptor],
    ] as const) {
      if (descriptor) Object.defineProperty(target, name, descriptor);
      else delete (target as unknown as Record<string, unknown>)[name];
    }
  }
});

// Vitest item 8: catches validation shown as saved, missing field/request details, and network failures shown as success.
test("shows field-level server errors and a distinct unreachable-server alert without success", async () => {
  const user = userEvent.setup();
  const validationRun = renderOpen({
    ...openTripRoutes(),
    [`PATCH /api/trips/${TRIP_ID}`]: apiError(
      422,
      "VALIDATION_FAILED",
      "The request is invalid.",
      "request-validation",
      { errors: [{ field: "budgetUsd", message: "Use an amount with two decimals." }] },
    ),
  });

  await screen.findByTestId("trip-summary");
  const budgetInput = screen.getByLabelText("Budget (USD)");
  await user.clear(budgetInput);
  await user.type(budgetInput, "12.00");
  await user.click(screen.getByRole("button", { name: "Save trip" }));
  const validationAlert = await screen.findByRole("alert");
  expect(validationAlert).toHaveTextContent("The request is invalid.");
  expect(validationAlert).toHaveTextContent("Request id: request-validation");
  expect(screen.getByTestId("field-error-budgetUsd")).toHaveTextContent("Use an amount with two decimals.");
  expect(screen.queryByTestId("saved")).not.toBeInTheDocument();
  expectRequest(
    validationRun.router.requests.find((entry) => entry.method === "PATCH"),
    "PATCH",
    `/api/trips/${TRIP_ID}`,
  );
  expectAuthorized(validationRun.router.requests);
  validationRun.unmount();

  const networkRun = renderOpen({
    ...openTripRoutes(),
    [`POST /api/trips/${TRIP_ID}/search`]: () => Promise.reject(new TypeError("connection refused")),
  });
  await screen.findByTestId("trip-summary");
  await user.selectOptions(screen.getByLabelText("Category"), "accommodation");
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
  expect(screen.queryByTestId("saved")).not.toBeInTheDocument();
  expectRequest(
    networkRun.router.requests.find((entry) => entry.path.endsWith("/search")),
    "POST",
    `/api/trips/${TRIP_ID}/search`,
    { category: "accommodation" },
  );
  expectAuthorized(networkRun.router.requests);
});

// Vitest item 9: catches role checks enforced only by the API while viewer write controls remain usable in the UI.
test("reloads as a viewer and hides every trip, participant, itinerary, booking, and document write control", async () => {
  const user = userEvent.setup();
  const organizer = trip({
    participants: [
      { userId: "ana", role: "organizer" },
      { userId: "ben", role: "viewer" },
    ],
  });
  const viewer = { ...organizer, role: "viewer" };
  const bookable = item();
  const unknownItem = item({
    id: "66666666-6666-4666-8666-666666666666",
    position: 1,
    offer: { id: "offer-unknown", title: "Unknown outcome hotel" },
    bookable: false,
    booking: booking("unknown", { itemId: "66666666-6666-4666-8666-666666666666" }),
  });
  const documents = [
    document("complete", { sha256: "abc123", sizeBytes: 42 }),
    document("failed", {
      id: "30000000-0000-4000-8000-000000000004",
      error: "The document could not be generated.",
    }),
  ];
  const routes = openTripRoutes(organizer, itinerary({ items: [bookable, unknownItem] }), documents);
  routes["GET /api/trips"] = (request) =>
    jsonResponse({ trips: [request.authorization === "Bearer token-ben" ? viewer : organizer] });
  routes[`GET /api/trips/${TRIP_ID}`] = (request) =>
    jsonResponse(request.authorization === "Bearer token-ben" ? viewer : organizer);
  routes[`POST /api/trips/${TRIP_ID}/search`] = jsonResponse({
    legs: [
      {
        destination: "Lisbon",
        status: "complete",
        options: [option({ offer: { id: "offer-viewer", title: "Viewer-visible hotel" } })],
        warnings: [],
        searchedAt: "2030-06-01T12:00:00Z",
      },
    ],
  });
  const { router } = renderOpen(routes);

  await screen.findByTestId("trip-summary");
  // As the organizer every control exists, so its absence for the viewer below is not vacuous.
  await screen.findByTestId(`item-${ITEM_ID}`);
  for (const name of ["Save trip", "Add participant", "Generate document", "Retry booking", "Retry"]) {
    expect(screen.getByRole("button", { name, exact: true })).toBeVisible();
  }
  for (const name of ["Move up", "Move down", "Remove", "Book"]) {
    expect(screen.getAllByRole("button", { name, exact: true }).length).toBeGreaterThan(0);
  }
  expect(within(screen.getByTestId("participants")).getByRole("button", { name: "Remove" })).toBeVisible();
  await user.selectOptions(screen.getByLabelText("Identity"), "ben");
  await screen.findByText("Your role: viewer");
  await user.selectOptions(screen.getByLabelText("Category"), "accommodation");
  await user.click(screen.getByRole("button", { name: "Search" }));
  const viewerOption = await screen.findByTestId("option-offer-viewer");

  await screen.findByTestId(`item-${ITEM_ID}`);
  await screen.findByTestId(`booking-66666666-6666-4666-8666-666666666666`);
  for (const name of [
    "Save trip",
    "Add participant",
    "Move up",
    "Move down",
    "Remove",
    "Book",
    "Retry booking",
    "Retry",
    "Generate document",
  ]) {
    expect(screen.queryByRole("button", { name, exact: true })).not.toBeInTheDocument();
  }
  expect(screen.queryByTestId("participants")).not.toBeInTheDocument();
  expect(screen.getByTestId("document-30000000-0000-4000-8000-000000000004")).toHaveAttribute("data-state", "failed");
  expect(screen.getByRole("button", { name: "Download" })).toBeVisible(); // a viewer still downloads a complete document
  expect(within(viewerOption).queryByRole("button", { name: "Add to itinerary" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Search" })).toBeVisible();
  expect(
    router.requests.some(
      (request) =>
        request.path === `/api/trips/${TRIP_ID}` && request.authorization === "Bearer token-ben",
    ),
  ).toBe(true);
  expectAuthorized(router.requests, ["token-ana", "token-ben"]);
});
