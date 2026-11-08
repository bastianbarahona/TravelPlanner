from sqlalchemy import select

from app.db.models import BookingAttempt


def latest(session, *, item_id=None, trip_id=None):
    """The newest attempt of one item (or None), or of every item of a trip, as {item_id: attempt}. An open attempt is
    always the newest one: a new attempt starts only after the previous one closed."""
    query = select(BookingAttempt).order_by(BookingAttempt.item_id, BookingAttempt.attempt_no.desc())
    if item_id is not None:
        return session.scalars(query.where(BookingAttempt.item_id == item_id).limit(1)).first()
    rows = session.scalars(query.where(BookingAttempt.trip_id == trip_id).distinct(BookingAttempt.item_id)).all()
    return {row.item_id: row for row in rows}
