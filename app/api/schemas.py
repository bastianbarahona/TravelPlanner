"""Request bodies. Every field the contract-style rules touch is a `str` checked by our own
functions, because Pydantic's Decimal and date fields accept more than the API may (ARCHITECTURE.md, Money)."""
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator
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


def _encodable(text):
    """A NUL or a lone surrogate would reach the provider URL or body (or the database) and fail as a 500."""
    if chr(0) in text:
        raise ValueError("Remove the NUL character.")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Use valid Unicode text.") from None
    return text


def _whole_number(text):
    # A query string. Digits only, so a huge version still answers 409. Beyond Python's int digit limit int() refuses it.
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]+", text):
        raise ValueError("Use a whole number, such as 3.")
    return int(text)


Amount = Annotated[str, _checked(parse_amount)]
Day = Annotated[str, _checked(parse_date)]
Travelers = Annotated[int, Field(strict=True, ge=1, le=20)]
# The contract allows 128 characters, counted as characters.
ProviderText = Annotated[str, Field(min_length=1, max_length=128), AfterValidator(_encodable)]
Version = Annotated[int, BeforeValidator(_whole_number)]  # for a query string. A body uses Field(strict=True)
Destinations = Annotated[list[Annotated[str, AfterValidator(clean_destination)]], Field(min_length=1)]


class Body(BaseModel):
    # forbid: a client-supplied organizerId or userId is never trusted. The token decides the caller.
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel)


class CreateTrip(Body):
    destinations: Destinations
    starts_on: Day
    ends_on: Day
    budget_usd: Amount
    travelers: Travelers


class PatchTrip(Body):
    version: int = Field(strict=True)
    # A default of None is never validated, so omitted is None and an explicit null is still refused.
    # Callers read `model_fields_set`: `Decimal("0.00")` is falsy, so truth is never tested.
    destinations: Destinations = None
    starts_on: Day = None
    ends_on: Day = None
    budget_usd: Amount = None
    travelers: Travelers = None

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


class SearchTrip(Body):
    """Everything but the category defaults from the trip, and travelers always comes from it."""

    category: Literal["transport", "accommodation", "activity"]
    origin: Annotated[str, AfterValidator(clean_destination)] = None
    destinations: Destinations = None
    starts_on: Day = None
    ends_on: Day = None
    provider_id: ProviderText = None


class AddItem(Body):
    version: int = Field(strict=True)
    offer_id: ProviderText


class Refresh(Body):
    version: int = Field(strict=True)


class Reorder(Body):
    version: int = Field(strict=True)
    item_ids: list[str]
