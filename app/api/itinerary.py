from fastapi import APIRouter, Depends

from app.api.deps import trip_editor, trip_member
from app.api.schemas import AddItem, Book, Refresh, Reorder, Retry, Version
from app.db.session import get_session
from app.services import booking, itinerary

# Reads for every participant, writes for the organizer and editors. A new route joins one and inherits its checks.
readers = APIRouter(prefix="/trips/{tripId}/itinerary", dependencies=[Depends(trip_member)])
editors = APIRouter(prefix="/trips/{tripId}/itinerary", dependencies=[Depends(trip_editor)])


@readers.get("")
def get_itinerary(access=Depends(trip_member), session=Depends(get_session)):
    return itinerary.itinerary_view(session, access.user, access.trip_id)


@editors.post("/items", status_code=201)
async def add_item(body: AddItem, access=Depends(trip_editor), session=Depends(get_session)):
    return await itinerary.add_item(session, access, body.version, body.offer_id)


@editors.delete("/items/{itemId}")
def remove_item(itemId: str, version: Version, access=Depends(trip_editor), session=Depends(get_session)):
    return itinerary.remove_item(session, access, version, itemId)


@editors.put("/order")
def reorder(body: Reorder, access=Depends(trip_editor), session=Depends(get_session)):
    return itinerary.reorder(session, access, body.version, body.item_ids)


@editors.post("/items/{itemId}/refresh")
async def refresh_item(itemId: str, body: Refresh, access=Depends(trip_editor), session=Depends(get_session)):
    return await itinerary.refresh_item(session, access, body.version, itemId)


@readers.get("/items/{itemId}/booking")
def get_booking(itemId: str, access=Depends(trip_member), session=Depends(get_session)):
    return booking.read(session, access, itemId)


@editors.post("/items/{itemId}/booking", status_code=201)
async def book_item(itemId: str, body: Book, access=Depends(trip_editor), session=Depends(get_session)):
    return await booking.book(session, access, itemId, body.expected_revision, body.expected_price_usd)


@editors.post("/items/{itemId}/booking/retry")
async def retry_booking(itemId: str, body: Retry = None, access=Depends(trip_editor), session=Depends(get_session)):
    return await booking.retry(session, access, itemId)
