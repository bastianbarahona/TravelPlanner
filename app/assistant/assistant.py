"""The built-in assistant. It reaches the application only through the MCP server (app/mcp/server.py), over real JSON-RPC
on HTTP, in process, with the caller's own Authorization header. It holds no token of its own, so it can read nothing its
user cannot, and it writes nothing: the server has no write tool. Two modes behind one route (ASSISTANT_MODE): a fixed policy
the evaluations test, and a model driving the same tools. Amounts are parsed and formatted only with app/money.py."""
import logging
import re

import anthropic
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app import config
from app.domain.errors import ApiError
from app.domain.rules import parse_date, parse_instant
from app.money import format_amount, parse_amount

logger = logging.getLogger("uvicorn.error")
# 127.0.0.1:8000, because the SDK's DNS-rebinding guard admits loopback hosts with a port only. The transport never opens a socket.
BASE_URL = "http://127.0.0.1:8000"
TOOLS = {"list_trips", "get_trip", "get_itinerary", "list_documents", "search_options"}
CATEGORIES = {
    "transport": ("flight", "flights", "train", "trains", "bus", "transport", "transfer"),
    "accommodation": ("hotel", "hotels", "stay", "accommodation", "lodging", "room"),
    "activity": ("activity", "activities", "tour", "tours", "museum", "excursion"),
}
CLOSING = "Prices are provider estimates, not bookings. I cannot change the itinerary or book; use the application to do that."
MAX_TURNS = 8
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Part of the TypeError anthropic 2.6.0 raises when no key is set (anthropic/_client.py; its text is itself in quotes),
# matched so no other TypeError is hidden.
NO_CREDENTIALS = "Could not resolve authentication method"
SYSTEM = """You are the assistant of a travel planning application, answering about trip {trip_id}.
You are read-only: you cannot change the itinerary, book or pay. Say so if asked to.
Get every fact from the tools, and pass trip_id "{trip_id}" to every tool that takes one.
Use only prices and availability a tool returned. Never invent an option, a price or an offer id.
If no option fits, or a tool failed, say so plainly and say what it reported.
Relate each option you suggest to the trip's dates, its remaining budget and the question. Prices are provider estimates, not bookings."""


def _text(result):
    return "\n".join(block.text for block in result.content if block.type == "text")


async def answer(app, authorization, trip_id, question):
    """`app` is served in process: the MCP client talks to /mcp through ASGI, with `authorization` on every request."""
    trip_id, calls = str(trip_id), []
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=BASE_URL, headers={"Authorization": authorization}
    ) as http, Client(streamable_http_client(f"{BASE_URL}/mcp/", http_client=http), cache=None) as client:
        # cache=None: the client's default cache could serve one user's result to another.

        async def call(name, arguments):
            result = await client.call_tool(name, arguments)
            calls.append({"name": name, "arguments": arguments, "isError": result.is_error})
            return result

        tools = (await client.list_tools()).tools
        if unknown := sorted({tool.name for tool in tools} - TOOLS):  # a guard: this assistant calls only the five
            text = f"I stopped: the tool server lists tools this assistant does not allow: {', '.join(unknown)}."
        elif config.ASSISTANT_MODE == "anthropic":
            text = await _ask_model(call, tools, trip_id, question)
        else:
            text = await _deterministic(call, trip_id, question)
    if text is None:  # raised out here: inside the client's task group an ApiError would arrive wrapped in an ExceptionGroup
        raise ApiError(503, "ASSISTANT_UNAVAILABLE", "The assistant's model cannot be reached. Try again shortly.")
    return {"tripId": trip_id, "mode": config.ASSISTANT_MODE, "answer": text, "toolCalls": calls}


async def _deterministic(call, trip_id, question):
    itinerary = await call("get_itinerary", {"trip_id": trip_id})
    if itinerary.is_error:
        return f"I could not read this trip: {_text(itinerary)}\n{CLOSING}"
    view = itinerary.structured_content
    budget = view["budget"]
    # Never below zero: an over-budget trip says so in the budget line, and nothing costs less than nothing.
    remaining = max(parse_amount("0.00"), parse_amount(budget["budgetUsd"]) - parse_amount(budget["totalUsd"]))
    categories = [
        category
        for category, words in CATEGORIES.items()
        if re.search(rf"\b({'|'.join(words)})\b", question, re.IGNORECASE)
    ]
    lines = []
    if categories:
        trip = await call("get_trip", {"trip_id": trip_id})  # the itinerary view has no trip dates
        if trip.is_error:
            lines.append(f"I could not read the trip dates: {_text(trip)}")
        dates = None if trip.is_error else trip.structured_content
        for category in categories:
            lines += await _options(call, trip_id, category, view, remaining, dates)
    budget_line = f"Budget: {budget['totalUsd']} of {budget['budgetUsd']} USD planned."
    if budget["overBudget"]:
        budget_line += f" Over budget by {budget['differenceUsd']} USD."
    lines.append(budget_line)
    lines += [f"Issue {issue['code']}: {issue['message']}" for issue in view["issues"]]
    if not categories:
        lines.append("Ask about transport, accommodation or activities to get options.")
    return "\n".join([*lines, CLOSING])


async def _options(call, trip_id, category, view, remaining, trip):
    result = await call("search_options", {"trip_id": trip_id, "category": category})
    if result.is_error:
        return [f"Search for {category} failed: {_text(result).removesuffix('.')}."]
    lines, options = [], []
    for leg in result.structured_content["legs"]:
        if leg["status"] == "failed":
            lines.append(f"Search for {category} in {leg['destination']} failed; no options from it.")
            continue
        if leg["status"] == "partial":
            lines.append(f"Results for {category} in {leg['destination']} may be incomplete.")
        options += leg["options"]
    planned = {item["offer"]["id"] for item in view["items"]}
    priced = [(parse_amount(option["offer"]["price"]["amount"]), option) for option in options]
    fits = sorted(
        (
            (price, option["offer"]["id"], option)
            for price, option in priced
            if option["bookable"] and option["offer"]["id"] not in planned and price <= remaining
        ),
        key=lambda found: found[:2],  # cheapest first, the offer id breaks a tie
    )[:3]
    shown_remaining = format_amount(remaining)
    for price, offer_id, option in fits:
        offer = option["offer"]
        line = f"- {offer_id}: {offer['title']}, {format_amount(price)} USD, {offer['startsAt']} to {offer['endsAt']}."
        line += f" Fits the remaining budget of {shown_remaining} USD."
        if (
            trip
            and parse_date(trip["startsOn"]) <= parse_instant(offer["startsAt"]).date()
            and parse_instant(offer["endsAt"]).date() <= parse_date(trip["endsOn"])
        ):  # the date as written, as the itinerary's own OUTSIDE_TRIP_DATES rule reads it
            line += " Within the trip dates."
        lines.append(line)
    if not fits:
        unavailable = sum(not option["bookable"] for _, option in priced)
        over = sum(option["bookable"] and price > remaining for price, option in priced)
        lines.append(
            f"No suitable {category} option found: {len(priced)} offers, {unavailable} unavailable or expired, "
            f"{over} over the remaining budget of {shown_remaining} USD."
        )
    return lines


async def _ask_model(call, tools, trip_id, question):
    """A manual tool-use loop: each tool_use block is one MCP tools/call. At most MAX_TURNS model turns.
    Returns the answer, or None when the model cannot be reached."""
    definitions = [{"name": tool.name, "description": tool.description, "input_schema": tool.input_schema} for tool in tools]
    messages = [{"role": "user", "content": question}]
    # ponytail: a client per question, with the SDK's own retries. Reuse one client, and stream, if latency matters.
    async with anthropic.AsyncAnthropic(timeout=60.0) as client:
        for _ in range(MAX_TURNS):
            try:
                response = await client.beta.messages.create(
                    model=config.ASSISTANT_MODEL,
                    max_tokens=4096,
                    system=SYSTEM.format(trip_id=trip_id),
                    tools=definitions,
                    tool_choice={"type": "auto"},
                    messages=messages,
                    fallbacks="default",  # server-side refusal fallback: the model's own default chain
                    betas=[FALLBACK_BETA],
                )
            except anthropic.AnthropicError as error:  # status, connection, timeout and credential errors: no answer, never a 500
                logger.warning("assistant model unavailable: %s: %s", type(error).__name__, error)
                return None
            except TypeError as error:
                if NO_CREDENTIALS not in str(error):  # any other TypeError is a bug here: it stays a 500
                    raise
                logger.warning("assistant model unavailable: no credentials")
                return None
            if response.stop_reason == "refusal":
                return "The model declined to answer this question."
            messages.append({"role": "assistant", "content": response.content})
            uses = [block for block in response.content if block.type == "tool_use"]
            if response.stop_reason == "end_turn":
                return "".join(block.text for block in response.content if block.type == "text")
            if response.stop_reason == "pause_turn":  # sent back as it is: the model carries on, and it counts as a turn
                continue
            if response.stop_reason != "tool_use" or not uses:  # max_tokens and the rest: a partial answer is never returned
                return f"I stopped before a complete answer (stop reason: {response.stop_reason})."
            results = []
            for use in uses:
                result = await call(use.name, use.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": use.id, "content": _text(result), "is_error": result.is_error}
                )
            messages.append({"role": "user", "content": results})
    return f"I stopped after {MAX_TURNS} model turns without a final answer."
