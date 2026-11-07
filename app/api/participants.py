from fastapi import APIRouter, Depends, Response

from app.api.deps import known_user, trip_organizer
from app.api.schemas import AddParticipant, ChangeRole
from app.db.session import get_session
from app.services import participants

router = APIRouter(prefix="/trips/{tripId}/participants", dependencies=[Depends(trip_organizer)])


@router.post("", status_code=201)
def add_participant(body: AddParticipant, access=Depends(trip_organizer), session=Depends(get_session)):
    return participants.add_participant(session, access.trip_id, body.user_id, body.role)


@router.patch("/{userId}")
def change_role(body: ChangeRole, user_id: str = Depends(known_user), access=Depends(trip_organizer), session=Depends(get_session)):
    return participants.change_role(session, access.trip_id, user_id, body.role)


@router.delete("/{userId}", status_code=204)
def remove_participant(user_id: str = Depends(known_user), access=Depends(trip_organizer), session=Depends(get_session)):
    participants.remove_participant(session, access.trip_id, user_id)
    return Response(status_code=204)
