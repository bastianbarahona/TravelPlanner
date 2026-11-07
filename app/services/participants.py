from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.models import TripParticipant
from app.domain.errors import ApiError, not_found


def _check_target(session, trip_id, user_id):
    """A missing row is a 404. The organizer's row is never changed or removed."""
    role = session.scalar(
        select(TripParticipant.role).where(TripParticipant.trip_id == trip_id, TripParticipant.user_id == user_id)
    )
    if role is None:
        raise not_found("Participant")
    if role == "organizer":
        raise ApiError(409, "ORGANIZER_IMMUTABLE", "The organizer's role cannot be changed or removed.")


def add_participant(session, trip_id, user_id, role):
    # ON CONFLICT DO NOTHING RETURNING: rowcount is -1 on this statement, so a returned row is the only signal.
    row = session.execute(
        insert(TripParticipant)
        .values(trip_id=trip_id, user_id=user_id, role=role)
        .on_conflict_do_nothing()
        .returning(TripParticipant.user_id, TripParticipant.role)
    ).one_or_none()
    if row is None:
        raise ApiError(409, "PARTICIPANT_EXISTS", "That user is already on the trip.")
    session.commit()
    return {"userId": row.user_id, "role": row.role}


def change_role(session, trip_id, user_id, role):
    _check_target(session, trip_id, user_id)
    # ponytail: last write wins, and participant writes do not bump the trip version. Add a version
    # to trip_participants if two organizers' edits ever need to conflict.
    row = session.execute(
        update(TripParticipant)
        .where(TripParticipant.trip_id == trip_id, TripParticipant.user_id == user_id)
        .values(role=role)
        .returning(TripParticipant.user_id, TripParticipant.role)
    ).one_or_none()
    if row is None:  # removed between the check and the update
        raise not_found("Participant")
    session.commit()
    return {"userId": row.user_id, "role": row.role}


def remove_participant(session, trip_id, user_id):
    _check_target(session, trip_id, user_id)
    session.execute(
        delete(TripParticipant).where(TripParticipant.trip_id == trip_id, TripParticipant.user_id == user_id)
    )
    session.commit()
