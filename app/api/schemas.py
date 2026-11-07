"""Request bodies. Every field the contract-style rules touch is a `str` checked by our own
functions, because Pydantic's Decimal and date fields accept more than the API may (ARCHITECTURE.md, Money)."""
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.config import IDENTITIES
from app.domain.rules import clean_destination, parse_date
from app.money import parse_amount


def _checked(parse):
    def check(text):
        parse(text)
        return text

    return AfterValidator(check)


def _known(user_id):
    if user_id not in IDENTITIES:
        raise ValueError("Use the id of a configured user.")
    return user_id


Amount = Annotated[str, _checked(parse_amount)]
Day = Annotated[str, _checked(parse_date)]
Destinations = Annotated[list[Annotated[str, AfterValidator(clean_destination)]], Field(min_length=1)]


class Body(BaseModel):
    # forbid: a client-supplied organizerId or userId is never trusted. The token decides the caller.
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel)


class CreateTrip(Body):
    destinations: Destinations
    starts_on: Day
    ends_on: Day
    budget_usd: Amount


class PatchTrip(Body):
    version: int = Field(strict=True)
    # A default of None is never validated, so omitted is None and an explicit null is still refused.
    # Callers read `model_fields_set`: `Decimal("0.00")` is falsy, so truth is never tested.
    destinations: Destinations = None
    starts_on: Day = None
    ends_on: Day = None
    budget_usd: Amount = None

    @model_validator(mode="after")
    def something_to_change(self):
        if self.model_fields_set <= {"version"}:
            raise ValueError("Send at least one field to change besides version.")
        return self


class AddParticipant(Body):
    user_id: Annotated[str, AfterValidator(_known)]
    role: Literal["viewer", "editor"]


class ChangeRole(Body):
    role: Literal["viewer", "editor"]
