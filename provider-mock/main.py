"""Travel provider mock: implements contracts/openAPI.json over HTTP.

Request bodies are validated against the contract file itself, so there is no
second copy of the schemas to drift from it. Handlers take the raw Request, which
also means FastAPI's default 422 can never be produced (A41).

A-numbers and section numbers refer to docs/CONTRACT_NOTES.md.
"""
import asyncio
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator
from starlette.exceptions import HTTPException

ROOT = Path(__file__).resolve().parent
CONTRACT = json.loads((ROOT.parent / "contracts" / "openAPI.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((ROOT / "fixtures.json").read_text(encoding="utf-8"))

DB_PATH = Path(os.environ["MOCK_DB_PATH"])
TOKENS = dict(pair.split(":", 1) for pair in os.environ["MOCK_TOKENS"].split(","))
QUOTE_TTL = timedelta(seconds=float(os.environ.get("MOCK_QUOTE_TTL_SECONDS", "900")))
SUPPLIER_DEADLINE = float(os.environ.get("MOCK_SUPPLIER_DEADLINE_SECONDS", "5"))
PENDING_DELAY = timedelta(seconds=float(os.environ.get("MOCK_PENDING_DELAY_SECONDS", "5")))
CONTROL_ENABLED = os.environ.get("MOCK_CONTROL_ENABLED") == "1"

RETRY_AFTER_SECONDS = "2"
BOOKING_FAILURE = {"code": "SUPPLIER_REJECTED", "message": "The supplier declined the simulated booking."}

logger = logging.getLogger("uvicorn.error")

# ponytail: control state lives in memory, tests never need it across a restart.
control = {"now": None, "fault": None}


def schema_validator(name):
    schema = {"$ref": f"#/components/schemas/{name}", "components": CONTRACT["components"]}
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


SEARCH_REQUEST = schema_validator("SearchRequest")
CREATE_BOOKING_REQUEST = schema_validator("CreateBookingRequest")


# --- Errors -----------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status, code, message, headers=None):
        self.status, self.code, self.message, self.headers = status, code, message, headers


def bad_request(message="Invalid request parameters or body."):
    return ApiError(400, "BAD_REQUEST", message)


def not_found():
    return ApiError(404, "NOT_FOUND", "No visible record exists for the supplied identifier at lookup time.")


def rate_limited():
    return ApiError(
        429, "RATE_LIMITED", "The provider rate limit was reached.", {"Retry-After": RETRY_AFTER_SECONDS}
    )


def unavailable():
    return ApiError(503, "PROVIDER_UNAVAILABLE", "The requested provider operation is temporarily unavailable.")


def error_response(status, code, message, headers=None):
    request_id = f"request-{uuid4().hex}"
    logger.warning("provider error status=%s code=%s requestId=%s", status, code, request_id)
    return JSONResponse(
        {"code": code, "message": message, "requestId": request_id}, status_code=status, headers=headers
    )


# --- Clock ------------------------------------------------------------------


def now():
    return control["now"] or datetime.now(UTC)


def to_timestamp(moment):
    return moment.isoformat().replace("+00:00", "Z")


# --- Storage ----------------------------------------------------------------


@contextmanager
def transaction():
    """One atomic unit (2.1, steps 4 to 9).

    BEGIN IMMEDIATE takes the write lock before the first read. A plain BEGIN fails
    with "database is locked" when concurrent requests upgrade from read to write.
    ponytail: reads take this lock too, because a read may settle a booking or
    refresh a quote. One writer at a time is the ceiling, fine for a mock.
    """
    db = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def seed_offers(db):
    """Existing offers keep their quote, so a restart never reuses a revision (A13)."""
    for offer in FIXTURES["offers"]:
        db.execute(
            "INSERT OR IGNORE INTO offers (id, doc) VALUES (?, ?)",
            (offer["id"], json.dumps({**offer, **new_quote(now())})),
        )


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.close()
    with transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS offers (id TEXT PRIMARY KEY, doc TEXT NOT NULL)")
        # Only accepted requests record a key or a reference (A6, A7), so both live on
        # the booking. Uniqueness is per caller (A39). Keys never expire (A18).
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY,
                caller TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                client_reference TEXT NOT NULL,
                request TEXT NOT NULL,
                doc TEXT NOT NULL,
                resolves_at TEXT,
                resolves_to TEXT,
                UNIQUE (caller, idempotency_key),
                UNIQUE (caller, client_reference)
            )
            """
        )
        seed_offers(db)


# --- Offers -----------------------------------------------------------------


def new_quote(moment):
    # Opaque and never reused, even after a fixture reset (A13).
    return {"revision": f"quote-{uuid4().hex}", "validUntil": to_timestamp(moment + QUOTE_TTL)}


def save_offer(db, offer):
    db.execute("UPDATE offers SET doc = ? WHERE id = ?", (json.dumps(offer), offer["id"]))


def current_offer(db, offer, moment):
    """Refresh an expired quote (A24, A25). An unchanged quote keeps its revision (A26)."""
    if moment >= datetime.fromisoformat(offer["validUntil"]):
        offer = {**offer, **new_quote(moment)}
        save_offer(db, offer)
    return offer


def matches(offer, query):
    """Party is exact (A22), locations fold case (A32), dates are literal local dates (A31)."""
    is_transport = query["category"] == "transport"
    return (
        offer["category"] == query["category"]
        and offer["destination"].casefold() == query["destination"].casefold()
        and (not is_transport or offer["origin"].casefold() == query["origin"].casefold())
        and offer["travelers"] == query["travelers"]
        and query["startsOn"] <= offer["startsAt"][:10]
        and offer["endsAt"][:10] <= query["endsOn"]
    )


def read_offers(query, suppliers):
    with transaction() as db:
        moment = now()
        offers = [json.loads(row["doc"]) for row in db.execute("SELECT doc FROM offers ORDER BY id")]
        return [
            current_offer(db, offer, moment)
            for offer in offers
            if offer["providerId"] in suppliers and matches(offer, query)
        ]


def queried_suppliers(query):
    suppliers = FIXTURES["suppliers"]
    provider_id = query.get("providerId")
    if provider_id is None:
        return [supplier for supplier, categories in suppliers.items() if query["category"] in categories]
    if provider_id not in suppliers:
        raise bad_request(f"Unknown providerId: {provider_id}.")
    return [provider_id] if query["category"] in suppliers[provider_id] else []  # A34


async def query_supplier(supplier, destination):
    """Returns a SupplierWarning code, or None if the supplier answered."""
    fault = FIXTURES["supplierFaults"].get(destination.casefold(), {}).get(supplier)
    if fault == "PROVIDER_TIMEOUT":
        await asyncio.sleep(SUPPLIER_DEADLINE)  # a supplier that never answers, cut at the deadline
    return fault


# --- Bookings ---------------------------------------------------------------


def find_booking(db, caller, column, value):
    return db.execute(f"SELECT * FROM bookings WHERE caller = ? AND {column} = ?", (caller, value)).fetchone()


def settle(db, row, moment):
    """Resolve a due pending booking on read, so the transition survives a restart.

    Only `status`, `updatedAt` and `failure` change. Terminal outcomes never reverse.
    """
    booking = json.loads(row["doc"])
    if booking["status"] == "pending" and moment >= datetime.fromisoformat(row["resolves_at"]):
        booking["status"] = row["resolves_to"]
        booking["updatedAt"] = row["resolves_at"]
        if booking["status"] == "failed":
            booking["failure"] = BOOKING_FAILURE
        db.execute("UPDATE bookings SET doc = ? WHERE id = ?", (json.dumps(booking), booking["id"]))
    return booking


def accept_booking(db, caller, key, request_body, canonical, offer, moment):
    outcome = FIXTURES["bookingOutcomes"].get(offer["id"], {"initial": "confirmed"})
    booking = {
        "id": f"booking-{uuid4().hex}",
        "clientReference": request_body["clientReference"],
        "offer": offer,
        "status": outcome["initial"],
        "createdAt": to_timestamp(moment),
        "updatedAt": to_timestamp(moment),
    }
    if booking["status"] == "failed":
        booking["failure"] = BOOKING_FAILURE
    is_pending = booking["status"] == "pending"
    db.execute(
        "INSERT INTO bookings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            booking["id"],
            caller,
            key,
            booking["clientReference"],
            canonical,
            json.dumps(booking),
            to_timestamp(moment + PENDING_DELAY) if is_pending else None,
            outcome["final"] if is_pending else None,
        ),
    )
    return booking


def create_booking(caller, key, request_body):
    """Steps 4 to 9 of 2.1, in order, as one transaction."""
    canonical = json.dumps(request_body, sort_keys=True, separators=(",", ":"))  # A9
    with transaction() as db:
        moment = now()
        existing = find_booking(db, caller, "idempotency_key", key)
        if existing:
            if existing["request"] != canonical:
                raise ApiError(
                    409, "IDEMPOTENCY_CONFLICT", "This key was already used with a different request body."
                )
            return 200, settle(db, existing, moment)
        if find_booking(db, caller, "client_reference", request_body["clientReference"]):
            raise ApiError(
                409, "REFERENCE_CONFLICT", "This client reference belongs to an existing booking request."
            )
        row = db.execute("SELECT doc FROM offers WHERE id = ?", (request_body["offerId"],)).fetchone()
        if row is None:
            raise not_found()
        offer = json.loads(row["doc"])
        if not offer["available"]:
            raise ApiError(409, "OFFER_UNAVAILABLE", "The offer is no longer available.")
        if (
            moment >= datetime.fromisoformat(offer["validUntil"])
            or request_body["expectedOfferRevision"] != offer["revision"]
            or request_body["expectedPrice"] != offer["price"]
        ):
            raise ApiError(
                409, "OFFER_CHANGED", "Price, quote revision, or quote validity changed; review the latest offer."
            )
        return 201, accept_booking(db, caller, key, request_body, canonical, offer, moment)


def read_booking(caller, column, value):
    with transaction() as db:
        row = find_booking(db, caller, column, value)
        if row is None:
            raise not_found()
        return settle(db, row, now())


# --- Request helpers --------------------------------------------------------


def authenticate(request):
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    caller = TOKENS.get(token) if scheme.lower() == "bearer" else None
    if caller is None:
        raise ApiError(401, "UNAUTHORIZED", "The provider token is missing or invalid.")
    return caller


def parse_body(raw, validator):
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        raise bad_request("The request body is not valid JSON.") from None
    error = next(validator.iter_errors(body), None)
    if error is not None:
        raise bad_request(f"Invalid request body: {error.message}")
    return body


def check_fault():
    """Step 3 of 2.1: after authentication and validation, before any state is read."""
    if control["fault"] == 429:
        raise rate_limited()
    if control["fault"] == 503:
        raise unavailable()


# --- Public API -------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


# redirect_slashes off: a trailing slash must not turn into a 307, which the contract does not declare.
app = FastAPI(
    title="Travel Provider Mock",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    redirect_slashes=False,
    lifespan=lifespan,
)


@app.exception_handler(ApiError)
async def on_api_error(_request, exc):
    return error_response(exc.status, exc.code, exc.message, exc.headers)


@app.exception_handler(HTTPException)
async def on_http_exception(_request, exc):
    code = "NOT_FOUND" if exc.status_code == 404 else "BAD_REQUEST"
    return error_response(exc.status_code, code, str(exc.detail), exc.headers)


@app.exception_handler(Exception)
async def on_unexpected_error(_request, exc):
    logger.error("unexpected provider error", exc_info=exc)
    return error_response(503, "PROVIDER_UNAVAILABLE", "The requested provider operation is temporarily unavailable.")


@app.get("/health")
def health():
    """Readiness, outside the contract. Takes the write lock, so an unwritable volume fails here."""
    with transaction():
        return {"status": "ok"}


@app.post("/v1/offers/search")
async def search_offers(request: Request):
    authenticate(request)
    query = parse_body(await request.body(), SEARCH_REQUEST)
    if query["endsOn"] < query["startsOn"]:  # both are validated YYYY-MM-DD, so text order is date order
        raise bad_request("endsOn must not be before startsOn.")
    queried = queried_suppliers(query)
    check_fault()
    # Concurrent, so one deadline bounds the whole search rather than adding up (A43).
    outcomes = await asyncio.gather(*(query_supplier(supplier, query["destination"]) for supplier in queried))
    failed = {supplier: code for supplier, code in zip(queried, outcomes) if code}
    if queried and len(failed) == len(queried):
        if len(queried) == 1 and "RATE_LIMITED" in failed.values():
            raise rate_limited()
        raise unavailable()  # A35
    offers = await asyncio.to_thread(read_offers, query, set(queried) - set(failed))
    return {
        "offers": offers,
        "partial": bool(failed),
        "warnings": [
            {"providerId": supplier, "code": code, "message": f"{supplier} did not complete the search."}
            for supplier, code in failed.items()
        ],
        "searchedAt": to_timestamp(now()),
    }


@app.get("/v1/offers/{offer_id}")
def get_offer(offer_id: str, request: Request):
    authenticate(request)
    check_fault()
    with transaction() as db:
        row = db.execute("SELECT doc FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if row is None:  # also covers ids over the 128 limit: none can exist (A41)
            raise not_found()
        return current_offer(db, json.loads(row["doc"]), now())


@app.post("/v1/bookings")
async def post_booking(request: Request):
    caller = authenticate(request)
    key = request.headers.get("idempotency-key", "")
    if not 1 <= len(key) <= 128:
        raise bad_request("Idempotency-Key is required, with 1 to 128 characters.")
    request_body = parse_body(await request.body(), CREATE_BOOKING_REQUEST)
    check_fault()
    # In a thread, so concurrent requests really race for the transaction.
    status, booking = await asyncio.to_thread(create_booking, caller, key, request_body)
    return JSONResponse(booking, status_code=status)


# `:path` because a reference may hold an encoded slash, which the server decodes before routing.
@app.get("/v1/bookings/by-reference/{client_reference:path}")
def get_booking_by_reference(client_reference: str, request: Request):
    caller = authenticate(request)
    check_fault()
    return read_booking(caller, "client_reference", client_reference)


@app.get("/v1/bookings/{booking_id}")
def get_booking(booking_id: str, request: Request):
    caller = authenticate(request)
    check_fault()
    return read_booking(caller, "id", booking_id)


# --- Control interface: outside the contract, off unless MOCK_CONTROL_ENABLED=1 ---

control_routes = APIRouter(prefix="/__control")


@control_routes.post("/reset")
def reset_fixtures():
    """The deliberate fixture reset of `:7`. Clears bookings, keys and references."""
    control.update(now=None, fault=None)
    with transaction() as db:
        db.execute("DELETE FROM bookings")
        db.execute("DELETE FROM offers")
        seed_offers(db)
    return {"status": "reset"}


@control_routes.post("/clock")
async def set_clock(request: Request):
    """Body {"now": "<date-time>"} pins mock time, {"now": null} returns to the wall clock (A46)."""
    pinned = (await request.json())["now"]
    control["now"] = datetime.fromisoformat(pinned) if pinned else None
    return {"now": pinned}


@control_routes.post("/fault")
async def set_fault(request: Request):
    """Body {"status": 429}, {"status": 503} or {"status": null}. Applies to every /v1 operation."""
    control["fault"] = (await request.json())["status"]
    return {"status": control["fault"]}


@control_routes.post("/offers/{offer_id}")
async def change_offer(offer_id: str, request: Request):
    """Merge fields into an offer, for example {"available": false}. Always a new revision (A26)."""
    changes = await request.json()
    with transaction() as db:
        row = db.execute("SELECT doc FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if row is None:
            raise not_found()
        offer = {**json.loads(row["doc"]), **changes, "revision": new_quote(now())["revision"]}
        save_offer(db, offer)
    return offer


if CONTROL_ENABLED:
    app.include_router(control_routes)
