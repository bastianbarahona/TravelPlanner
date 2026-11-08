from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from app.db.models import Trip, TripDestination, TripParticipant
from app.db.session import begin_snapshot
from app.domain.errors import ApiError, ValidationFailed, not_found
from app.domain.rules import date_errors, parse_date
from app.money import format_amount, parse_amount


def trip_views(session, caller, *filters):
    """Trips as the API shows them to `caller`: 3 queries however many trips match."""
    trips = session.execute(
        select(Trip.id, Trip.starts_on, Trip.ends_on, Trip.budget_usd, Trip.version, Trip.travelers)
        .where(*filters)
        .order_by(Trip.starts_on, Trip.id)
    ).all()
    ids = [trip.id for trip in trips]
    destinations, participants = {}, {}
    for row in session.execute(
        select(TripDestination.trip_id, TripDestination.name)
        .where(TripDestination.trip_id.in_(ids))
        .order_by(TripDestination.position)
    ):
        destinations.setdefault(row.trip_id, []).append(row.name)
    for row in session.execute(
        select(TripParticipant.trip_id, TripParticipant.user_id, TripParticipant.role).where(
            TripParticipant.trip_id.in_(ids)
        )
    ):
        participants.setdefault(row.trip_id, []).append({"userId": row.user_id, "role": row.role})
    views = []
    for trip in trips:
        # Sorted here, not by Postgres: a database collation would decide the order otherwise.
        people = sorted(participants.get(trip.id, []), key=lambda person: person["userId"])
        views.append(
            {
                "id": str(trip.id),
                "destinations": destinations.get(trip.id, []),
                "startsOn": trip.starts_on.isoformat(),
                "endsOn": trip.ends_on.isoformat(),
                "budgetUsd": format_amount(trip.budget_usd),
                "travelers": trip.travelers,
                "version": trip.version,
                "role": next((person["role"] for person in people if person["userId"] == caller), None),
                "participants": people,
            }
        )
    return views


def _snapshot_views(session, caller, *filters):
    """trip_views in its own REPEATABLE READ transaction: a version never pairs with another version's destinations."""
    begin_snapshot(session)
    try:
        return trip_views(session, caller, *filters)
    finally:
        session.rollback()


def create_trip(session, caller, destinations, starts_on, ends_on, budget_usd, travelers):
    starts_on, ends_on = parse_date(starts_on), parse_date(ends_on)
    if errors := date_errors(starts_on, ends_on, check_start=True):
        raise ValidationFailed(errors)
    trip_id = uuid4()
    # Trip, destinations, then the organizer, in one transaction: a failure leaves nothing behind.
    session.execute(
        insert(Trip).values(
            id=trip_id, starts_on=starts_on, ends_on=ends_on, budget_usd=parse_amount(budget_usd), travelers=travelers
        )
    )
    session.execute(
        insert(TripDestination),
        [{"trip_id": trip_id, "position": i, "name": name} for i, name in enumerate(destinations)],
    )
    session.execute(insert(TripParticipant).values(trip_id=trip_id, user_id=caller, role="organizer"))
    session.commit()
    return _snapshot_views(session, caller, Trip.id == trip_id)[0]


def list_trips(session, caller):
    mine = select(TripParticipant.trip_id).where(TripParticipant.user_id == caller)
    return _snapshot_views(session, caller, Trip.id.in_(mine))


def get_trip(session, caller, trip_id):
    """Inside the caller's own transaction: a write's pre-check, or a read that already holds a snapshot."""
    views = trip_views(session, caller, Trip.id == trip_id)
    if not views:
        raise not_found()
    return views[0]


def read_trip(session, caller, trip_id):
    """What the API shows: one snapshot of the trip."""
    views = _snapshot_views(session, caller, Trip.id == trip_id)
    if not views:
        raise not_found()
    return views[0]


def version_conflict(session, caller, trip_id):
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "The trip changed since you read it. Review details.current and send its version.",
        {"current": read_trip(session, caller, trip_id)},
    )


def update_trip(session, caller, trip_id, version, changes):
    """`changes` holds only the fields the client sent, by attribute name."""
    stored = get_trip(session, caller, trip_id)
    if version != stored["version"]:
        raise version_conflict(session, caller, trip_id)
    starts_on = parse_date(changes["starts_on"]) if "starts_on" in changes else parse_date(stored["startsOn"])
    ends_on = parse_date(changes["ends_on"]) if "ends_on" in changes else parse_date(stored["endsOn"])
    # A36: a trip under way stays editable, so the past-start rule only judges a start date that changes.
    if errors := date_errors(starts_on, ends_on, check_start=starts_on.isoformat() != stored["startsOn"]):
        # `stored` may be stale: the read above can block while a concurrent write commits. Only a version that
        # still matches proves the dates were current, and a moved one is a 409, as in the itinerary writes.
        if session.scalar(select(Trip.version).where(Trip.id == trip_id)) != version:
            raise version_conflict(session, caller, trip_id)
        raise ValidationFailed(errors)
    values = {"starts_on": starts_on, "ends_on": ends_on}
    if "budget_usd" in changes:
        values["budget_usd"] = parse_amount(changes["budget_usd"])
    if "travelers" in changes:
        values["travelers"] = changes["travelers"]
    # Optimistic lock. A concurrent writer that got here first leaves zero rows to update.
    updated = session.execute(
        update(Trip).where(Trip.id == trip_id, Trip.version == version).values(**values, version=Trip.version + 1)
    )
    if updated.rowcount == 0:
        raise version_conflict(session, caller, trip_id)
    if "destinations" in changes:  # only after the guarded update won, in the same transaction
        session.execute(delete(TripDestination).where(TripDestination.trip_id == trip_id))
        session.execute(
            insert(TripDestination),
            [{"trip_id": trip_id, "position": i, "name": name} for i, name in enumerate(changes["destinations"])],
        )
    session.commit()
    return read_trip(session, caller, trip_id)
