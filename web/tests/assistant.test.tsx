import "@testing-library/jest-dom/vitest";

import type { ComponentType } from "react";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import * as AppModule from "../src/App";
import {
  TRIP_ID,
  apiError,
  fetchRouter,
  itinerary,
  jsonResponse,
  trip,
  type Route,
} from "./fixtures";

const appExports = AppModule as unknown as {
  App?: ComponentType;
  default?: ComponentType;
};
const App = (appExports.App ?? appExports.default) as ComponentType;

function openTripRoutes(tripView = trip()): Record<string, Route> {
  return {
    "GET /api/trips": jsonResponse({ trips: [tripView] }),
    [`GET /api/trips/${TRIP_ID}`]: jsonResponse(tripView),
    [`GET /api/trips/${TRIP_ID}/itinerary`]: jsonResponse(itinerary()),
    [`GET /api/trips/${TRIP_ID}/documents`]: jsonResponse({ documents: [] }),
  };
}

function renderOpen(routes: Record<string, Route>) {
  window.location.hash = `#/trips/${TRIP_ID}`;
  const router = fetchRouter(routes);
  vi.stubGlobal("fetch", router.fetch);
  const rendered = render(<App />);
  return { ...rendered, router };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
  window.location.hash = "";
});

// Catches a malformed assistant request, missing in-flight state, unsafe HTML rendering, and reordered tool results.
test("asks the assistant and renders its answer and tool calls verbatim", async () => {
  const user = userEvent.setup();
  let release!: (response: Response) => void;
  const held = new Promise<Response>((resolve) => (release = resolve));
  const { router } = renderOpen({
    ...openTripRoutes(),
    [`POST /api/trips/${TRIP_ID}/assistant`]: () => held,
  });

  await screen.findByTestId("trip-summary");
  const panel = screen.getByTestId("assistant");
  expect(panel.tagName).toBe("SECTION");
  expect(within(panel).getByRole("heading", { name: "Assistant" })).toBeVisible();
  const question = within(panel).getByLabelText("Question");
  expect(question).toHaveAttribute("maxlength", "1000");
  await user.type(question, "Find a hotel near the center");
  await user.click(within(panel).getByRole("button", { name: "Ask" }));

  expect(within(panel).getByText("Asking...")).toBeVisible();
  expect(within(panel).getByRole("button", { name: "Ask" })).toBeDisabled();
  const request = router.requests.find((entry) => entry.path.endsWith("/assistant"));
  expect(request).toMatchObject({
    method: "POST",
    path: `/api/trips/${TRIP_ID}/assistant`,
    authorization: "Bearer token-ana",
  });
  expect(request?.body).toEqual({ question: "Find a hotel near the center" });

  await act(async () => {
    release(
      jsonResponse({
        tripId: TRIP_ID,
        mode: "deterministic",
        answer: "Central stay available\n<b>x</b>",
        toolCalls: [
          { name: "get_itinerary", arguments: { trip_id: TRIP_ID }, isError: false },
          {
            name: "search_options",
            arguments: { trip_id: TRIP_ID, category: "accommodation" },
            isError: true,
          },
        ],
      }),
    );
  });

  const answer = await screen.findByTestId("assistant-answer");
  expect(within(answer).getByText("Central stay available", { exact: true }).tagName).toBe("P");
  expect(within(answer).getByText("<b>x</b>", { exact: true }).tagName).toBe("P");
  expect(answer.getElementsByTagName("b")).toHaveLength(0);
  expect(screen.getByTestId("assistant-mode")).toHaveTextContent("Mode: deterministic");
  const tools = screen.getByTestId("assistant-tools");
  expect(tools.tagName).toBe("UL");
  const calls = within(tools).getAllByTestId("tool-call");
  expect(calls).toHaveLength(2);
  expect(calls[0]).toHaveAttribute("data-error", "false");
  expect(calls[0]).toHaveTextContent(/^get_itinerary$/);
  expect(calls[1]).toHaveAttribute("data-error", "true");
  expect(calls[1]).toHaveTextContent(/^search_options \(tool error\)$/);
});

// Catches assistant outages that lose server diagnostics, leave stale answers, or keep Ask disabled.
test("shows an assistant unavailable error and permits retry", async () => {
  const user = userEvent.setup();
  let callCount = 0;
  renderOpen({
    ...openTripRoutes(),
    [`POST /api/trips/${TRIP_ID}/assistant`]: () => {
      callCount += 1;
      if (callCount === 2) {
        return apiError(
          503,
          "ASSISTANT_UNAVAILABLE",
          "The assistant is temporarily unavailable.",
          "request-assistant-unavailable",
        );
      }
      return jsonResponse({
        tripId: TRIP_ID,
        mode: "deterministic",
        answer: callCount === 1 ? "First answer" : "Retry answer",
        toolCalls: [],
      });
    },
  });

  await screen.findByTestId("trip-summary");
  await user.type(screen.getByLabelText("Question"), "Suggest an activity");
  await user.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByTestId("assistant-answer")).toHaveTextContent("First answer");

  await user.type(screen.getByLabelText("Question"), "Try another activity");
  await user.click(screen.getByRole("button", { name: "Ask" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("The assistant is temporarily unavailable.");
  expect(alert).toHaveTextContent("Request id: request-assistant-unavailable");
  expect(screen.queryByText("First answer")).not.toBeInTheDocument();
  expect(screen.queryByTestId("assistant-answer")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();

  await user.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByTestId("assistant-answer")).toHaveTextContent("Retry answer");
});

// Catches a response from the previous identity leaking into the newly selected identity's view.
test("discards an in-flight assistant response when identity changes", async () => {
  const user = userEvent.setup();
  let release!: (response: Response) => void;
  const held = new Promise<Response>((resolve) => (release = resolve));
  const { router } = renderOpen({
    ...openTripRoutes(),
    [`POST /api/trips/${TRIP_ID}/assistant`]: () => held,
  });

  await screen.findByTestId("trip-summary");
  await user.type(screen.getByLabelText("Question"), "What should I do?");
  await user.click(screen.getByRole("button", { name: "Ask" }));
  expect(screen.getByText("Asking...")).toBeVisible();

  await user.selectOptions(screen.getByLabelText("Identity"), "ben");
  await vi.waitFor(() =>
    expect(router.requests.some((entry) => entry.authorization === "Bearer token-ben")).toBe(true),
  );
  await act(async () => {
    release(
      jsonResponse({
        tripId: TRIP_ID,
        mode: "deterministic",
        answer: "Stale answer",
        toolCalls: [],
      }),
    );
    await held;
  });

  expect(screen.queryByTestId("assistant-answer")).not.toBeInTheDocument();
  expect(screen.queryByText("Asking...")).not.toBeInTheDocument();
});

// Catches question validation failures that are shown only as a page-level error instead of beside the field.
test("shows the server question validation error beside the field", async () => {
  const user = userEvent.setup();
  renderOpen({
    ...openTripRoutes(),
    [`POST /api/trips/${TRIP_ID}/assistant`]: apiError(
      422,
      "VALIDATION_FAILED",
      "The request is invalid.",
      "request-question-validation",
      { errors: [{ field: "question", message: "Question must contain useful text." }] },
    ),
  });

  await screen.findByTestId("trip-summary");
  await user.type(screen.getByLabelText("Question"), "?");
  await user.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByTestId("field-error-question")).toHaveTextContent(
    "Question must contain useful text.",
  );
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("The request is invalid.");
  expect(alert).toHaveTextContent("Request id: request-question-validation");
  expect(screen.queryByTestId("assistant-answer")).not.toBeInTheDocument();
});

// Catches the assistant being hidden or blocked by the trip page's viewer write restrictions.
test("allows a viewer to see and ask the assistant", async () => {
  const user = userEvent.setup();
  const viewerTrip = trip({
    role: "viewer",
    participants: [{ userId: "ana", role: "viewer" }],
  });
  const { router } = renderOpen({
    ...openTripRoutes(viewerTrip),
    [`POST /api/trips/${TRIP_ID}/assistant`]: jsonResponse({
      tripId: TRIP_ID,
      mode: "deterministic",
      answer: "Viewer answer",
      toolCalls: [],
    }),
  });

  await screen.findByText("Your role: viewer");
  const panel = screen.getByTestId("assistant");
  expect(within(panel).getByRole("heading", { name: "Assistant" })).toBeVisible();
  await user.type(within(panel).getByLabelText("Question"), "What can I visit?");
  await user.click(within(panel).getByRole("button", { name: "Ask" }));

  expect(await screen.findByTestId("assistant-answer")).toHaveTextContent("Viewer answer");
  const tools = screen.getByTestId("assistant-tools");
  expect(tools.tagName).toBe("UL");
  expect(tools).toHaveTextContent("No tools were called.");
  expect(within(tools).queryAllByTestId("tool-call")).toHaveLength(0);
  expect(router.requests.find((entry) => entry.path.endsWith("/assistant"))).toMatchObject({
    method: "POST",
    body: { question: "What can I visit?" },
    authorization: "Bearer token-ana",
  });
});
