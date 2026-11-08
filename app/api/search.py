from fastapi import APIRouter, Depends

from app.api.deps import trip_member
from app.api.schemas import SearchTrip
from app.db.session import get_session
from app.services import search

router = APIRouter(prefix="/trips/{tripId}/search", dependencies=[Depends(trip_member)])


@router.post("")
async def search_trip(body: SearchTrip, access=Depends(trip_member), session=Depends(get_session)):
    return await search.search_trip(session, access, body)
