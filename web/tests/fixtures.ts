export const TRIP_ID = "11111111-1111-4111-8111-111111111111";
export const ITEM_ID = "22222222-2222-4222-8222-222222222222";
export const DOCUMENT_ID = "33333333-3333-4333-8333-333333333333";

type Json = Record<string, unknown>;

export function trip(overrides: Json = {}) {
  return {
    id: TRIP_ID,
    destinations: ["Lisbon"],
    startsOn: "2030-06-10",
    endsOn: "2030-06-13",
    budgetUsd: "1000.00",
    travelers: 2,
    version: 1,
    role: "organizer",
    participants: [{ userId: "ana", role: "organizer" }],
    ...overrides,
  };
}

export function offer(overrides: Json = {}) {
  return {
    id: "offer-hotel-001",
    providerId: "supplier-a",
    category: "accommodation",
    title: "Three-night central hotel stay",
    description: "One room for two travelers, breakfast included.",
    destination: "Lisbon",
    startsAt: "2030-06-10T15:00:00+01:00",
    endsAt: "2030-06-13T11:00:00+01:00",
    travelers: 2,
    price: { amount: "420.00", currency: "USD" },
    available: true,
    restrictions: ["Non-refundable simulated booking"],
    revision: "revision-1",
    validUntil: "2030-06-09T12:15:00Z",
    ...overrides,
  };
}

export function option(overrides: Json = {}) {
  const shownOffer = offer((overrides.offer as Json | undefined) ?? {});
  const rest = { ...overrides };
  delete rest.offer;
  return {
    offer: shownOffer,
    priceKind: "estimate",
    expired: false,
    bookable: shownOffer.available,
    ...rest,
  };
}

export function booking(state: string, overrides: Json = {}) {
  const providerBooking = ["pending", "confirmed", "failed"].includes(state)
    ? {
        booking: {
          id: `provider-booking-${state}`,
          clientReference: `trip-${TRIP_ID}-item-${ITEM_ID}-attempt-1`,
          status: state,
          offer: offer(),
          createdAt: "2030-06-01T12:00:00Z",
          updatedAt: "2030-06-01T12:00:00Z",
          ...(state === "failed"
            ? { failure: { code: "SUPPLIER_FAILED", message: "The supplier refused the booking." } }
            : {}),
        },
      }
    : {};
  return {
    id: `attempt-${state}`,
    itemId: ITEM_ID,
    attempt: 1,
    state,
    clientReference: `trip-${TRIP_ID}-item-${ITEM_ID}-attempt-1`,
    requested: {
      offerId: "offer-hotel-001",
      expectedOfferRevision: "revision-1",
      expectedPriceUsd: "420.00",
    },
    requestedBy: "ana",
    createdAt: "2030-06-01T12:00:00+00:00",
    nextStep: `Next step for ${state}.`,
    ...providerBooking,
    ...overrides,
  };
}

export function item(overrides: Json = {}) {
  const shownOffer = offer((overrides.offer as Json | undefined) ?? {});
  const rest = { ...overrides };
  delete rest.offer;
  return {
    id: ITEM_ID,
    position: 0,
    offer: shownOffer,
    priceKind: "estimate",
    expired: false,
    bookable: shownOffer.available,
    ...rest,
  };
}

export function budget(overrides: Json = {}) {
  return {
    currency: "USD",
    lines: [],
    estimatedUsd: "0.00",
    confirmedUsd: "0.00",
    totalUsd: "0.00",
    budgetUsd: "1000.00",
    differenceUsd: "1000.00",
    overBudget: false,
    roundingRule:
      "Amounts are exact sums of the provider's two-decimal prices, so nothing is rounded when they are added. Any amount that needs a third decimal rounds half up.",
    ...overrides,
  };
}

export function itinerary(overrides: Json = {}) {
  return {
    tripId: TRIP_ID,
    version: 1,
    items: [],
    issues: [],
    rules: ["Every price is an estimate until a booking confirms it."],
    budget: budget(),
    ...overrides,
  };
}

export function document(state: string, overrides: Json = {}) {
  return {
    id: DOCUMENT_ID,
    tripId: TRIP_ID,
    tripVersion: 1,
    state,
    requestedBy: "ana",
    requestedAt: "2030-06-01T12:00:00.000000Z",
    attempts: state === "waiting" ? 0 : 1,
    nextStep: `Next step for ${state}.`,
    ...overrides,
  };
}

export function apiError(
  status: number,
  code: string,
  message: string,
  requestId: string,
  details?: Json,
) {
  return jsonResponse(
    { code, message, requestId, ...(details === undefined ? {} : { details }) },
    status,
  );
}

export function jsonResponse(body: unknown, status = 200, headers: HeadersInit = {}) {
  const responseHeaders = new Headers(headers);
  responseHeaders.set("content-type", "application/json");
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: responseHeaders,
  });
}

export type RecordedRequest = {
  method: string;
  path: string;
  body: unknown;
  authorization: string | null;
};

export type RouteHandler = (
  request: RecordedRequest,
) => Response | Promise<Response>;

export type Route = Response | RouteHandler;

export function fetchRouter(routes: Record<string, Route>) {
  const requests: RecordedRequest[] = [];
  const fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const source = input instanceof Request ? input : undefined;
    const url = new URL(source?.url ?? String(input), "http://localhost");
    const method = (init.method ?? source?.method ?? "GET").toUpperCase();
    const headers = new Headers(source?.headers);
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
    const text =
      init.body === undefined
        ? source
          ? await source.clone().text()
          : ""
        : String(init.body);
    const request: RecordedRequest = {
      method,
      path: `${url.pathname}${url.search}`,
      body: text ? JSON.parse(text) : undefined,
      authorization: headers.get("authorization"),
    };
    requests.push(request);
    if (!request.path.startsWith("/api/")) {
      throw new Error(`Unexpected non-API fetch: ${method} ${request.path}`);
    }
    const route = routes[`${method} ${request.path}`];
    if (!route) {
      throw new Error(`Unhandled fetch: ${method} ${request.path}`);
    }
    return typeof route === "function" ? route(request) : route.clone();
  };
  return { fetch, requests };
}
