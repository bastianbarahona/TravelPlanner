from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import AfterValidator, StringConstraints

from app.api.deps import trip_member
from app.api.schemas import Body, _encodable
from app.assistant import assistant
from app.db.session import get_session

# Every participant may ask: the assistant reads what the asker could read, through MCP, and changes nothing.
router = APIRouter(prefix="/trips/{tripId}/assistant", dependencies=[Depends(trip_member)])


class Ask(Body):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000), AfterValidator(_encodable)]


@router.post("")
async def ask(request: Request, body: Ask, access=Depends(trip_member), session=Depends(get_session)):
    # trip_member's query left a transaction open on this request's session: end it before the model is waited on.
    await run_in_threadpool(session.rollback)
    return await assistant.answer(request.app, request.headers["authorization"], access.trip_id, body.question)
