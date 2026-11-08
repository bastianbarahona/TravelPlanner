from fastapi import APIRouter, Depends

from app.api.deps import current_user, trip_member, trip_organizer
from app.api.schemas import CreateTrip, PatchTrip
from app.db.session import get_session
from app.services import trips

# Three routers, three permission levels. A new route joins one and inherits its checks.
collection = APIRouter(prefix="/trips", dependencies=[Depends(current_user)])
readers = APIRouter(prefix="/trips/{tripId}", dependencies=[Depends(trip_member)])
organizers = APIRouter(prefix="/trips/{tripId}", dependencies=[Depends(trip_organizer)])


@collection.post("", status_code=201)
def create_trip(body: CreateTrip, user: str = Depends(current_user), session=Depends(get_session)):
    return trips.create_trip(session, user, body.destinations, body.starts_on, body.ends_on, body.budget_usd, body.travelers)


@collection.get("")
def list_trips(user: str = Depends(current_user), session=Depends(get_session)):
    return {"trips": trips.list_trips(session, user)}


@readers.get("")
def get_trip(access=Depends(trip_member), session=Depends(get_session)):
    return trips.read_trip(session, access.user, access.trip_id)


@organizers.patch("")
def update_trip(body: PatchTrip, access=Depends(trip_organizer), session=Depends(get_session)):
    changes = body.model_dump(exclude={"version"}, exclude_unset=True)
    return trips.update_trip(session, access.user, access.trip_id, body.version, changes)
