"""Authentication and permission dependencies, in the order the brief fixes: 401, then 404, then 403.

Routers carry them in `dependencies=[...]`, so a route added to a router cannot forget them.
"""
from typing import NamedTuple
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select

from app.config import IDENTITIES, TOKENS
from app.db.models import TripParticipant
from app.db.session import get_session
from app.domain.errors import ApiError, not_found


class Access(NamedTuple):
    user: str
    role: str
    trip_id: UUID


def bearer_user(authorization):
    """The user a bearer token names, or None. `current_user` and the /mcp guard (app/main.py) both read it."""
    scheme, _, token = authorization.partition(" ")
    # ponytail: a plain dict lookup of the test identities in APP_TOKENS. Not constant time, which does
    # not matter for fixed test tokens. A real identity provider replaces this function, and the places that read
    # the configured identities: `_known` in app/api/schemas.py and `known_user` below.
    return TOKENS.get(token) if scheme.lower() == "bearer" else None


def unauthorized():
    return ApiError(401, "UNAUTHORIZED", "The bearer token is missing or invalid.", headers={"WWW-Authenticate": "Bearer"})


def current_user(request: Request):
    user = bearer_user(request.headers.get("authorization", ""))
    if user is None:
        raise unauthorized()
    return user


def membership(session, user, tripId):
    """Nonparticipant, missing trip and malformed id are the same 404. The id is parsed here:
    a raw string on a UUID column raises DataError, a 500. The API (`trip_member`) and the MCP tools both call it."""
    try:
        trip_id = UUID(tripId)
    except ValueError:
        raise not_found() from None
    if str(trip_id) != tripId.lower():  # UUID() also takes braces, urn: and underscores
        raise not_found()
    role = session.scalar(
        select(TripParticipant.role).where(TripParticipant.trip_id == trip_id, TripParticipant.user_id == user)
    )
    if role is None:
        raise not_found()
    return Access(user, role, trip_id)


def trip_member(tripId: str, user: str = Depends(current_user), session=Depends(get_session)):
    return membership(session, user, tripId)


def trip_organizer(access: Access = Depends(trip_member)):
    # Trip fields and participants belong to the organizer alone. Editors change the itinerary (trip_editor).
    if access.role != "organizer":
        raise ApiError(403, "FORBIDDEN", "Only the trip organizer can do this.")
    return access


def trip_editor(access: Access = Depends(trip_member)):
    if access.role not in ("organizer", "editor"):
        raise ApiError(403, "FORBIDDEN", "Only the trip organizer or an editor can do this.")
    return access


def known_user(userId: str):
    """Checked before any query: a NUL in a query is a 500."""
    if userId not in IDENTITIES:
        raise not_found("Participant")
    return userId
