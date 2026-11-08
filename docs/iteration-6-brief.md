# Iteration 6 brief: MCP layer, assistant and evaluations

**As built** (2026-11-08): implemented as written, with the departures in the "As built" and "After cross-review" paragraphs below, 53 new tests and 39 killed mutations (A184 to A222). Codex wrote the first round of tests and reviewed `app/`. It then hit a usage limit, so `qa-ux` fixed and extended the tests. The record is `docs/STATUS.md` 2.14.

The single spec for both authors. Written by the Director on 2026-11-08 (UTC-3), from `docs/STATUS.md` 2.2, with the five decisions the owner approved that day (D1 to D5). If this brief and `CLAUDE.md` disagree, `CLAUDE.md` wins: stop and report.

Stories: US-11 (grounded recommendations), US-12 (authorized trip information through MCP), and the assistant half of US-02. Read first: `CLAUDE.md`, `ARCHITECTURE.md` (Refusals and roles, Search, Itinerary and its version, Estimates and confirmed prices), `docs/prd.md` US-02, US-11, US-12, and `docs/candidate-guide.md` "Testing and evaluation evidence".

## Who does what

| Author | Writes | Never touches |
|---|---|---|
| Claude Sonnet (`dev-principal`) | `app/` (new `app/mcp/server.py`, `app/assistant/assistant.py`, `app/api/assistant.py`; `app/api/deps.py`, `app/main.py`, `app/config.py`, `app/requirements.txt`), `.env.example`, `docker-compose.yml` (environment only) | `tests/`, `contracts/`, docs |
| Codex | `tests/` (new `test_app_mcp.py`, `test_app_assistant.py`, `assistant_evals.py`, `test_assistant_evals.py`; `conftest.py` / `app_support.py` where needed) | `app/`, `contracts/`, docs. Codex writes the tests **from this brief, without reading `app/`** |

The Director runs every check and writes the docs. Codex's sandbox denies Python and Docker, so Codex reports what it could not run. No migration: the assistant stores nothing, so there is no `0005`. Mutation ids start at A184.

## Decisions (approved by the owner)

1. **SDK and transport (D1).** The official SDK, `mcp==2.3.0` (`from mcp.server.mcpserver import MCPServer`). Streamable HTTP, `stateless_http=True`, `json_response=True`, mounted in the same FastAPI app at `/mcp` (`app.mount("/mcp", server.streamable_http_app(streamable_http_path="/", ...))`; the MCP endpoint is `/mcp/`). `app/main.py`'s lifespan wraps its `yield` in `async with server.session_manager.run()`. The built-in assistant reaches it through real MCP JSON-RPC over HTTP, in process: `httpx2.AsyncClient(transport=httpx2.ASGITransport(app=request.app), base_url="http://127.0.0.1:8000")` passed to `streamable_http_client(".../mcp/", http_client=...)`, wrapped in `mcp.Client(..., cache=None)`. `cache=None` is required: the client's default cache could serve one user's result to another. The SDK's default DNS-rebinding protection allows only loopback hosts; keep it. Probed by the Director: the caller's `Authorization` header reaches the tool through `ctx.headers`, sync tools run in a worker thread, a `ToolError` reaches the client as `is_error=True` with its text, and the in-process `Client(server)` carries no headers, so identity cannot ride on it.
2. **LLM and deterministic mode (D2).** `ASSISTANT_MODE`, `deterministic` by default (no network, no key), or `anthropic`. Any other value fails at startup. The deterministic policy is below and is the contract the evaluations test. The `anthropic` mode uses the official `anthropic` SDK (pin the version installed, today 2.6.0; check every call against the installed SDK, because the reference the Director read documents 1.x), `ASSISTANT_MODEL` defaulting to `claude-sonnet-5-5`, credentials from the SDK's own resolution (`ANTHROPIC_API_KEY`).
3. **Identity and permissions (D3).** No tool takes a user. The identity is the API's bearer token, read from the MCP HTTP request. A request to `/mcp` without a valid token answers 401 with the `Error` shape and `WWW-Authenticate: Bearer`, before MCP runs (a small ASGI wrapper around the mounted app, using the same token lookup as `current_user`). Every tool that takes a `trip_id` resolves membership through the same function as `trip_member`: move its body out of the dependency into `membership(session, user, trip_id_text) -> Access`, which both call. Nonparticipant, missing trip and malformed id give the same refusal. The assistant forwards the caller's own `Authorization` header and never holds a token of its own, so it can never read more than its user. The `/openapi.json` permission walk does not see a `Mount`, so `/mcp` has its own permission tests (below).
4. **Tools (D4).** Exactly five, all read-only, each with `ToolAnnotations(readOnlyHint=True)`:

   | Tool | Arguments | Returns | Same as |
   |---|---|---|---|
   | `list_trips` | none | `{"trips": [...]}` | `GET /trips` |
   | `get_trip` | `trip_id` | the trip view | `GET /trips/{tripId}` |
   | `get_itinerary` | `trip_id` | items with booking states, `issues`, `rules`, `budget` | `GET /trips/{tripId}/itinerary` |
   | `list_documents` | `trip_id` | `{"documents": [...]}` | `GET /trips/{tripId}/documents` |
   | `search_options` | `trip_id`, `category`, optional `origin`, `destinations`, `starts_on`, `ends_on`, `provider_id` | `{"legs": [...]}` | `POST /trips/{tripId}/search` |

   Each tool calls the same service function as its route, with the same session handling (`search_options` is async: database work in the thread pool, no transaction open while the provider answers, as `search_trip` already does). `search_options` validates its arguments with the API's `SearchTrip` model. An `ApiError` becomes `ToolError(f"{code}: {message}")`; a validation error becomes `ToolError("VALIDATION_FAILED: ...")` with the field names and never the input. Any other exception stays the SDK's generic `Error executing tool ...`, which leaks nothing. A tool returns its view as structured JSON.
5. **Evaluations (D5).** Fixed cases scored by binary checks, below. Deterministic mode runs in pytest and must score 100%. Model mode runs the same cases with the same checks from `python tests/assistant_evals.py --mode anthropic`, prints a score per check, is never part of CI, and its cost is measured once a key is supplied.

## The assistant route

`POST /trips/{tripId}/assistant`, on a router with `dependencies=[Depends(trip_member)]`, so every participant may use it, a nonparticipant gets the API's 404 and a missing token 401. It appears in `/openapi.json`.

- Body: `{"question": str}`, 1 to 1000 characters after stripping, no other field (422 `VALIDATION_FAILED` otherwise).
- 200: `{"tripId": str, "mode": "deterministic" | "anthropic", "answer": str, "toolCalls": [{"name": str, "arguments": object, "isError": bool}]}`. `toolCalls` lists every MCP `tools/call`, in order, as sent and as answered.
- 503 `ASSISTANT_UNAVAILABLE` when `ASSISTANT_MODE=anthropic` and the model cannot be reached or has no credentials. Never a 500 for a model failure.
- Nothing is stored, and the route never changes trip state.

## Deterministic policy

Every step goes through the MCP client. Amounts are parsed and formatted only with `app/money.py`.

1. `tools/list`. If any listed tool is not one of the five, stop and answer with an error (a guard, not a feature).
2. `get_itinerary(trip_id)`. If it is an error, the answer starts with `I could not read this trip:` and the tool's error text, and nothing else is called.
3. Categories, from the question, case-insensitive, whole words, in this order:
   - `transport`: flight, flights, train, trains, bus, transport, transfer
   - `accommodation`: hotel, hotels, stay, accommodation, lodging, room
   - `activity`: activity, activities, tour, tours, museum, excursion
4. With no category, no search: the answer gives the budget line and the issues (steps 7 and 8), then `Ask about transport, accommodation or activities to get options.`
5. For each category, `search_options(trip_id, category)`. A tool error adds `Search for <category> failed: <error text>.` For each leg: `failed` adds `Search for <category> in <destination> failed; no options from it.`; `partial` adds `Results for <category> in <destination> may be incomplete.`
6. Candidates per category, from the non-failed legs: `bookable` is true, the offer's id is not already in the itinerary, and its price is at most the remaining budget, which is `budgetUsd - totalUsd` from the itinerary's `budget`. Cheapest first, at most 3. Each is one line: `- <offer id>: <title>, <price amount> USD, <startsAt> to <endsAt>.` followed by its reasons: `Fits the remaining budget of <remaining> USD.` and, when it is true, `Within the trip dates.` (the local date of `startsAt` and `endsAt`, as written, within the trip's `startsOn` and `endsOn`). With no candidate: `No suitable <category> option found: <n> offers, <u> unavailable or expired, <o> over the remaining budget of <remaining> USD.`
7. Budget line: `Budget: <totalUsd> of <budgetUsd> USD planned.` and `Over budget by <differenceUsd> USD.` when `overBudget`.
8. Each itinerary issue: `Issue <code>: <message>`.
9. Always last: `Prices are provider estimates, not bookings. I cannot change the itinerary or book; use the application to do that.`

No amount and no offer id may appear in an answer unless it came from a tool result, apart from the remaining budget, which step 6 computes from two of them.

**As built, decided by the Director on 2026-11-08 from Sonnet's report:** with a category, `get_trip` is called after `get_itinerary`, because "Within the trip dates" needs the trip's dates. The remaining budget is clamped at `0.00`. An error text that ends with a period loses it inside a sentence. A transport search without an origin is sent as step 5 says, and its `VALIDATION_FAILED` is disclosed: the trip records no origin (A30), and parsing one from the question is out of scope. A tool error's text carries the SDK's prefix, `Error executing tool <name>: `, before `CODE: message`.

**After cross-review, decided by the Director on 2026-11-08** (each reproduced first):

- `/mcp` without the slash gets the same 401 guard as `/mcp/` (it answered 404).
- Every tool's arguments fail the same way, whoever catches them: a wrong type, a missing argument and an unknown argument all answer `VALIDATION_FAILED: <argument>: <message>`, never the SDK's own text and never the input (the SDK's check answered `1 validation error for search_optionsArguments ... input_value=...`). The tools keep their typed schemas for the model.
- Model mode stops only on `end_turn` (the answer) and `refusal` (the declined text). `pause_turn` is sent back and counts as a turn. Any other stop reason, `max_tokens` included, answers `I stopped before a complete answer (stop reason: <reason>).` and never returns the partial text. Only the SDK's typed errors and its missing-credentials `TypeError` become 503; any other `TypeError` is a bug and stays a 500.
- MCP protocol errors after authentication keep JSON-RPC's shape, not the API's `Error` shape. `ARCHITECTURE.md` says so.

## Model mode

A manual tool-use loop with the `anthropic` SDK: the MCP `tools/list` result becomes the request's `tools` (name, description, input schema), each `tool_use` block becomes one MCP `tools/call`, and its result goes back as a `tool_result` with the tool's JSON text and `is_error` when the MCP result is an error. At most 8 model turns; past that the answer says it stopped. Stop on `end_turn`; on `refusal` the answer says the model declined. Use the server-side refusal fallback the SDK reference recommends for `claude-sonnet-5-5` (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`) if the installed SDK supports it, and say in the report whether it did. The system prompt states the trip id, that the assistant is read-only, must use only retrieved prices and availability, must say when no option fits or a tool failed, and must relate options to dates, budget and the question. `tool_choice` stays `auto`.

## Tests that must exist, and must be able to fail

Over real HTTP, with the app, Postgres and the provider mock as real processes, as every app suite does. Use the mock's fixtures and its control interface for faults (`/__control/fault`), and `ProviderStub` only for a shape the mock cannot produce. A test that only asserts a canned response is not evidence. The MCP client in tests is the SDK's own `mcp.Client` over `streamable_http_client` against the app's `/mcp/`, with the user's `Authorization` header.

`tests/test_app_mcp.py`

1. `/mcp/` with no token and with a wrong token answers 401 with the `Error` shape and `WWW-Authenticate`; with a valid token, `tools/list` returns exactly the five names, each `readOnlyHint` true.
2. For each tool with a `trip_id`: organizer, editor and viewer get the same data as the matching route (for `get_itinerary`, compare fields that do not depend on the clock); a valid user who is not a participant, a missing trip and a malformed id each get `is_error` with `NOT_FOUND`, and the three texts are identical. A trip id taken from another user's trip grants nothing.
3. `list_trips` shows only the caller's trips.
4. No tool writes: after calling all five as the organizer, the trip `version`, the items, the booking attempts and the documents are unchanged, and the provider mock received no booking.
5. `search_options` with the mock faulted at 503 returns a result whose legs are `failed`, not an error and not options; invalid arguments return `VALIDATION_FAILED` naming the field.

`tests/test_app_assistant.py`

6. The route refuses a missing token (401), a nonparticipant (404, same body as any trip route), an empty or over-long question and an extra field (422); a viewer gets 200.
7. `toolCalls` shows the real calls: at least `get_itinerary`, and `search_options` only when the question names a category.
8. Calling the route changes no state (same check as 4).
9. `ASSISTANT_MODE=anthropic` with no credentials answers 503 `ASSISTANT_UNAVAILABLE`, not a 500 and not a made-up answer; an invalid `ASSISTANT_MODE` stops the app at startup.

`tests/assistant_evals.py` and `tests/test_assistant_evals.py`

The cases, each a seeded trip, a user, a question and an optional mock fault. Every case is scored by every check that applies to it:

| Case | Setup | Must hold |
|---|---|---|
| E1 grounded hotel | Lisbon trip, generous budget, viewer asks for a hotel | `search_options(accommodation)` called; at least one recommendation; grounding check passes |
| E2 nothing fits | Same trip, budget already spent by the itinerary | `No suitable accommodation`; no recommended offer id |
| E3 provider down | Mock faulted at 503 | `failed` stated; no offer id in the answer |
| E4 conflicts | Itinerary with an overlap or a missing stay | each issue code in the answer |
| E5 no category | A question with no category word | no `search_options` call; the budget line |
| E6 not a participant | A user outside the trip | route 404; through MCP, `NOT_FOUND` and no trip data |
| E7 read-only | Any case | `tools/list` holds no write tool; state unchanged |

Checks, each a function the runner reports by name: `called_expected_tools`, `grounded` (every `\d+\.\d{2}` amount and every offer id in the answer appears in a tool result, the remaining budget aside), `says_no_option`, `discloses_failure`, `mentions_issues`, `refuses_outsider`, `read_only`. The runner prints one row per case and check, and the totals. `test_assistant_evals.py` runs it in deterministic mode and fails on any failed check. Each check also needs a control that proves it can fail: a hand-written bad answer (an invented price, a missing "No suitable", a hidden failure) that the check rejects.

**Done when:** all of the above pass, the mutation check kills every A184+ entry with a killing test in `APP_KILLERS`, `docker compose up` works from clean, CI is green, and `docs/STATUS.md` is updated.
