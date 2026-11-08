import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "http://localhost:8000";
const CONTROL = "http://localhost:4010/__control";

function auth(user = "ana") {
  return { Authorization: `Bearer token-${user}` };
}

async function resetProvider(request: APIRequestContext) {
  const response = await request.post(`${CONTROL}/reset`);
  expect(response.ok(), `provider reset returned ${response.status()}`).toBeTruthy();
}

async function setFault(request: APIRequestContext, status: number | null) {
  const response = await request.post(`${CONTROL}/fault`, { data: { status } });
  expect(response.ok(), `provider fault control returned ${response.status()}`).toBeTruthy();
}

async function changeOffer(
  request: APIRequestContext,
  offerId: string,
  fields: Record<string, unknown>,
) {
  const response = await request.post(`${CONTROL}/offers/${offerId}`, { data: fields });
  expect(response.ok(), `provider offer control returned ${response.status()}`).toBeTruthy();
}

async function createTrip(
  request: APIRequestContext,
  destinations: string[] = ["Lisbon"],
) {
  const response = await request.post(`${API}/trips`, {
    headers: auth(),
    data: {
      destinations,
      startsOn: "2030-06-10",
      endsOn: "2030-06-13",
      budgetUsd: "2000.00",
      travelers: 2,
    },
  });
  expect(response.status()).toBe(201);
  return response.json() as Promise<{
    id: string;
    version: number;
    budgetUsd: string;
  }>;
}

async function searchAccommodation(page: Page) {
  await page.getByLabel("Category").selectOption("accommodation");
  await page.getByRole("button", { name: "Search" }).click();
}

async function addOffer(page: Page, offerId: string) {
  const quote = page.getByTestId(`option-${offerId}`);
  await expect(quote).toBeVisible();
  await quote.getByRole("button", { name: "Add to itinerary" }).click();
  await expect(page.locator(`[data-testid^="item-"][data-offer-id="${offerId}"]`)).toBeVisible();
}

async function reviewAndConfirm(page: Page, offerId: string) {
  const item = page.locator(`[data-testid^="item-"][data-offer-id="${offerId}"]`);
  await item.getByRole("button", { name: "Book", exact: true }).click();
  const review = page.getByTestId("booking-review");
  await expect(review).toBeVisible();
  await review.getByRole("button", { name: "Confirm booking" }).click();
  return item;
}

test.beforeEach(async ({ request }) => {
  await resetProvider(request);
});

// Playwright item 1: catches create/search/add flows that do not update budget state or survive a reload.
test("creates a Lisbon trip, adds accommodation, and preserves the itinerary", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Destinations").fill("Lisbon");
  await page.getByLabel("Start date").fill("2030-06-10");
  await page.getByLabel("End date").fill("2030-06-13");
  await page.getByLabel("Budget (USD)").fill("2000.00");
  await page.getByLabel("Travelers").fill("2");
  await page.getByRole("button", { name: "Create trip" }).click();

  await expect(page).toHaveURL(/#\/trips\/[0-9a-f-]+$/);
  await expect(page.getByTestId("trip-summary")).toContainText("Lisbon");
  await searchAccommodation(page);
  await addOffer(page, "offer-hotel-001");
  const item = page.locator('[data-testid^="item-"][data-offer-id="offer-hotel-001"]');
  await expect(item).toContainText("Three-night central hotel stay");
  await expect(page.getByTestId("budget")).toContainText("420.00");
  await expect(page.getByTestId("budget")).toContainText("Within budget");

  await page.reload();
  await expect(page.locator('[data-testid^="item-"][data-offer-id="offer-hotel-001"]')).toContainText(
    "Three-night central hotel stay",
  );
  await expect(page.getByTestId("budget")).toContainText("420.00");
});

// Playwright item 2: catches partial-without-options rendered as empty and failed/empty destinations collapsed together.
test("keeps Porto, Coimbra, Evora, and an unknown city's search outcomes distinct", async ({ page, request }) => {
  const trip = await createTrip(request, ["Porto", "Coimbra", "Evora", "Atlantis"]);
  await page.goto(`/#/trips/${trip.id}`);
  await searchAccommodation(page);

  const porto = page.getByTestId("leg-Porto");
  await expect(porto).toHaveAttribute("data-status", "partial");
  await expect(porto).toContainText("Partial results: some suppliers did not answer");
  const coimbra = page.getByTestId("leg-Coimbra");
  await expect(coimbra).toHaveAttribute("data-status", "partial");
  await expect(coimbra).toContainText("Partial results: some suppliers did not answer");
  await expect(coimbra).not.toContainText("No options found");
  await expect(page.getByTestId("leg-Evora")).toContainText("Search failed");
  await expect(page.getByTestId("leg-Atlantis")).toContainText("No options found");
});

// Playwright item 3: catches stale edits that overwrite concurrent changes, lose typed input, or keep resending the old version.
test("recovers a stale trip edit and succeeds on the second save", async ({ page, request }) => {
  const trip = await createTrip(request);
  await page.goto(`/#/trips/${trip.id}`);
  await expect(page.getByTestId("trip-summary")).toContainText(`Version ${trip.version}`);

  const concurrent = await request.patch(`${API}/trips/${trip.id}`, {
    headers: auth(),
    data: { version: trip.version, budgetUsd: "1900.00" },
  });
  expect(concurrent.status()).toBe(200);
  const changed = (await concurrent.json()) as { version: number };

  const budget = page.getByLabel("Budget (USD)");
  await budget.fill("1850.00");
  await page.getByRole("button", { name: "Save trip" }).click();
  const conflict = page.getByTestId("conflict");
  await expect(conflict).toContainText("Not saved");
  await expect(conflict).toContainText("The trip changed since you read it.");
  await expect(conflict).toContainText(/Request id: request-/);
  await expect(page.getByTestId("trip-summary")).toContainText(`Version ${changed.version}`);
  await expect(budget).toHaveValue("1850.00");

  await page.getByRole("button", { name: "Save trip" }).click();
  await expect(page.getByTestId("saved")).toContainText("Saved");
  await expect(page.getByTestId("trip-summary")).toContainText(`Version ${changed.version + 1}`);
});

// Playwright item 4: catches lost polling transitions, failed/unknown conflation, unavailable confirmation, and retry creating a new intent.
test("shows pending, confirmed, failed, unavailable, unknown, and retried booking outcomes side by side", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  await changeOffer(request, "offer-hotel-005", { available: true });
  const trip = await createTrip(request);
  await page.goto(`/#/trips/${trip.id}`);
  await searchAccommodation(page);
  for (const offerId of ["offer-hotel-002", "offer-hotel-004", "offer-hotel-005", "offer-hotel-001"]) {
    await addOffer(page, offerId);
  }

  const pendingItem = await reviewAndConfirm(page, "offer-hotel-002");
  await expect(pendingItem.locator('[data-testid^="booking-"][data-state]')).toHaveAttribute("data-state", "pending");
  await expect(pendingItem).toContainText("Pending: the provider has not decided yet");

  const failedItem = await reviewAndConfirm(page, "offer-hotel-004");
  await expect(failedItem.locator('[data-testid^="booking-"][data-state]')).toHaveAttribute("data-state", "failed");
  await expect(failedItem).toContainText("Failed: the provider refused this booking");

  await changeOffer(request, "offer-hotel-005", { available: false });
  const unavailableItem = page.locator('[data-testid^="item-"][data-offer-id="offer-hotel-005"]');
  await unavailableItem.getByRole("button", { name: "Book", exact: true }).click();
  const unavailableReview = page.getByTestId("booking-review");
  await expect(unavailableReview).toContainText("This option is unavailable");
  await expect(unavailableReview.getByRole("button", { name: "Confirm booking" })).toHaveCount(0);
  await unavailableReview.getByRole("button", { name: "Cancel" }).click();

  const unknownItem = page.locator('[data-testid^="item-"][data-offer-id="offer-hotel-001"]');
  await unknownItem.getByRole("button", { name: "Book", exact: true }).click();
  const review = page.getByTestId("booking-review");
  await expect(review).toBeVisible();
  await setFault(request, 503);
  await review.getByRole("button", { name: "Confirm booking" }).click();
  const unknownBooking = unknownItem.locator('[data-testid^="booking-"][data-state]');
  await expect(unknownBooking).toHaveAttribute("data-state", "unknown");
  await expect(unknownBooking).toContainText("Unknown: we could not confirm the outcome. This is not a failure.");
  await setFault(request, null);
  await unknownItem.getByRole("button", { name: "Retry booking" }).click();
  await expect(unknownBooking).toHaveAttribute("data-state", "confirmed");

  await expect(pendingItem.locator('[data-testid^="booking-"][data-state]')).toHaveAttribute("data-state", "confirmed", {
    timeout: 30_000,
  });
  await expect(failedItem.locator('[data-testid^="booking-"][data-state]')).toHaveAttribute("data-state", "failed");
  await expect(unavailableItem).toBeVisible();
  await expect(page.getByTestId("issues")).toContainText("Sold-out boutique hotel is no longer available.");
  await expect(unknownBooking).toHaveAttribute("data-state", "confirmed");
});

// Playwright item 5: catches document polling that needs a reload or complete documents that cannot be downloaded.
test("generates a document to completion without reload and downloads it", async ({ page, request }) => {
  const trip = await createTrip(request);
  await page.goto(`/#/trips/${trip.id}`);
  await page.getByRole("button", { name: "Generate document" }).click();

  const document = page.locator('[data-testid^="document-"]').first();
  await expect(document).toBeVisible();
  await expect(document).toHaveAttribute("data-state", "complete", { timeout: 30_000 });
  await expect(document).toContainText("Complete");
  const downloadPromise = page.waitForEvent("download");
  await document.getByRole("button", { name: "Download" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^trip-.*\.html$/);
});

// Playwright item 6: catches viewer authorization represented only server-side while write controls remain in the browser.
test("shows a viewer the trip without any write control", async ({ page, request }) => {
  const trip = await createTrip(request);
  const participant = await request.post(`${API}/trips/${trip.id}/participants`, {
    headers: auth(),
    data: { userId: "ben", role: "viewer" },
  });
  expect(participant.status(), await participant.text()).toBe(201);

  await page.goto(`/#/trips/${trip.id}`);
  await searchAccommodation(page);
  await addOffer(page, "offer-hotel-001");
  await page.getByLabel("Identity").selectOption("ben");
  await expect(page.getByTestId("trip-summary")).toContainText("Your role: viewer");
  await expect(page.locator('[data-testid^="item-"][data-offer-id="offer-hotel-001"]')).toBeVisible();
  await searchAccommodation(page);
  await expect(page.getByTestId("option-offer-hotel-002")).toBeVisible();

  for (const name of [
    "Save trip",
    "Add participant",
    "Move up",
    "Move down",
    "Remove",
    "Book",
    "Retry booking",
    "Generate document",
    "Add to itinerary",
  ]) {
    await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
  }
  await expect(page.getByRole("button", { name: "Search" })).toBeVisible();
});

// Playwright item 7: catches an assistant flow that works only with mocked frontend responses or omits result details.
test("asks the deterministic assistant for accommodation", async ({ page, request }) => {
  const trip = await createTrip(request);
  await page.goto(`/#/trips/${trip.id}`);

  const assistant = page.getByTestId("assistant");
  await expect(assistant).toBeVisible();
  await assistant.getByLabel("Question").fill("Find accommodation within budget");
  await assistant.getByRole("button", { name: "Ask" }).click();

  await expect(assistant.getByTestId("assistant-mode")).toHaveText("Mode: deterministic", {
    timeout: 15_000,
  });
  const toolCalls = assistant.getByTestId("tool-call");
  await expect(toolCalls.filter({ hasText: /^search_options$/ })).toHaveCount(1);
  for (const toolCall of await toolCalls.all()) {
    await expect(toolCall).toHaveAttribute("data-error", "false");
  }
  const answer = assistant.getByTestId("assistant-answer");
  await expect(answer).toHaveText(/\S/);
  await expect(answer).not.toContainText("failed");
});
