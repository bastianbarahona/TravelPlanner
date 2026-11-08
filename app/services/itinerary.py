"""Itinerary use cases. Every write bumps trips.version with the guarded UPDATE: there is no second version.

Order of a write: the version is compared in Python first (a stale request spends no provider call, and a huge
version never reaches the UPDATE), then the rest, then the guarded UPDATE, then the item write, in one transaction.
No connection is held while the provider is called: the work before and after it runs in the thread pool,
and the work before it ends with a rollback.
"""
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, func, insert, select, update

from app.db.models import ItineraryItem, Trip
from app.domain.errors import ApiError, ValidationFailed, not_found
from app.db.session import begin_snapshot
from app.domain.itinerary import view
from app.providers import client
from app.services.trips import get_trip


def itinerary_view(session, caller, trip_id):
    begin_snapshot(session)  # the version, the trip and the items come from one snapshot
    trip = get_trip(session, caller, trip_id)
    rows = session.execute(
        select(ItineraryItem.id, ItineraryItem.position, ItineraryItem.offer)
        .where(ItineraryItem.trip_id == trip_id)
        .order_by(ItineraryItem.position)
    ).all()
    session.rollback()
    items = [{"id": str(row.id), "position": row.position, "offer": row.offer} for row in rows]
    return view(trip, items, datetime.now(UTC))


def _conflict(session, access):
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "The itinerary changed since you read it. Review details.current and send its version.",
        {"current": itinerary_view(session, access.user, access.trip_id)},
    )


def _check_version(session, access, version):
    if version != session.scalar(select(Trip.version).where(Trip.id == access.trip_id)):
        raise _conflict(session, access)


def _bump(session, access, version):
    """The optimistic lock. A concurrent writer that got here first leaves zero rows to update."""
    won = session.execute(
        update(Trip).where(Trip.id == access.trip_id, Trip.version == version).values(version=Trip.version + 1)
    )
    if won.rowcount == 0:
        raise _conflict(session, access)


def _item(session, access, version, text):
    """The item's id, position and snapshot. A malformed, missing or other-trip id is the same 404.

    A missing row may only mean a concurrent write removed it. Every write bumps the version with its change, so a
    version still equal to the request's proves nothing was committed since the first check: only then is it a 404."""
    try:
        item_id = UUID(text)
    except ValueError:
        raise not_found() from None
    row = None
    if str(item_id) == text.lower():  # UUID() also takes braces, urn: and underscores
        row = session.execute(
            select(ItineraryItem.position, ItineraryItem.offer).where(
                ItineraryItem.id == item_id, ItineraryItem.trip_id == access.trip_id
            )
        ).one_or_none()
    if row is None:
        if str(item_id) == text.lower():
            _check_version(session, access, version)
        raise not_found()
    return item_id, row.position, row.offer


def _precheck(session, access, version):
    _check_version(session, access, version)
    session.rollback()


def _append(session, access, version, offer):
    _bump(session, access, version)
    # Positions are always 0..n-1, so the count is the next one. The bump holds the trip row until commit,
    # so no other writer can insert in between.
    position = session.scalar(select(func.count()).where(ItineraryItem.trip_id == access.trip_id))
    session.execute(insert(ItineraryItem).values(id=uuid4(), trip_id=access.trip_id, position=position, offer=offer))
    session.commit()
    return itinerary_view(session, access.user, access.trip_id)


async def add_item(session, access, version, offer_id):
    await run_in_threadpool(_precheck, session, access, version)
    # The client sends only an id. The snapshot is what the provider says now, never a client-supplied offer or price.
    offer = await client.get_offer(offer_id)
    if not offer["available"]:
        raise ApiError(409, "OFFER_UNAVAILABLE", "The provider says this offer is not available.")
    return await run_in_threadpool(_append, session, access, version, offer)


def remove_item(session, access, version, text):
    _check_version(session, access, version)
    item_id, position, _offer = _item(session, access, version, text)
    _bump(session, access, version)
    session.execute(delete(ItineraryItem).where(ItineraryItem.id == item_id))
    session.execute(  # close the gap, so positions stay 0..n-1. The unique constraint is deferred to commit.
        update(ItineraryItem)
        .where(ItineraryItem.trip_id == access.trip_id, ItineraryItem.position > position)
        .values(position=ItineraryItem.position - 1)
    )
    session.commit()
    return itinerary_view(session, access.user, access.trip_id)


def reorder(session, access, version, item_ids):
    _check_version(session, access, version)
    current = session.scalars(select(ItineraryItem.id).where(ItineraryItem.trip_id == access.trip_id)).all()
    if sorted(item_ids) != sorted(str(item_id) for item_id in current):
        _check_version(session, access, version)  # the items may have changed after the first check: that is a 409
        raise ValidationFailed(
            [{"field": "itemIds", "message": "Send the id of every itinerary item exactly once, in the new order."}]
        )
    _bump(session, access, version)
    for position, item_id in enumerate(item_ids):
        session.execute(update(ItineraryItem).where(ItineraryItem.id == UUID(item_id)).values(position=position))
    session.commit()
    return itinerary_view(session, access.user, access.trip_id)


def _stored_quote(session, access, version, text):
    _check_version(session, access, version)
    item_id, _position, offer = _item(session, access, version, text)
    session.rollback()
    return item_id, offer


def _replace_quote(session, access, version, item_id, fresh):
    _check_version(session, access, version)  # something changed while we asked the provider
    if fresh != _item(session, access, version, str(item_id))[2]:
        _bump(session, access, version)
        session.execute(update(ItineraryItem).where(ItineraryItem.id == item_id).values(offer=fresh))
        session.commit()
    return itinerary_view(session, access.user, access.trip_id)


async def refresh_item(session, access, version, text):
    item_id, stored = await run_in_threadpool(_stored_quote, session, access, version, text)
    fresh = await client.get_offer(stored["id"])  # a 404 is OFFER_GONE: the snapshot and the version stay
    return await run_in_threadpool(_replace_quote, session, access, version, item_id, fresh)
