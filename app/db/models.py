import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Trip(Base):
    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint("ends_on >= starts_on", name="ck_trips_dates"),
        CheckConstraint("budget_usd >= 0", name="ck_trips_budget"),
        CheckConstraint("travelers BETWEEN 1 AND 20", name="ck_trips_travelers"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    starts_on: Mapped[date]
    ends_on: Mapped[date]
    budget_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    version: Mapped[int] = mapped_column(server_default=text("1"))
    travelers: Mapped[int]


class TripDestination(Base):
    __tablename__ = "trip_destinations"

    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class TripParticipant(Base):
    __tablename__ = "trip_participants"
    __table_args__ = (
        CheckConstraint("role IN ('organizer','editor','viewer')", name="ck_participants_role"),
        Index("uq_one_organizer", "trip_id", unique=True, postgresql_where=text("role = 'organizer'")),
    )

    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(Text)


class ItineraryItem(Base):
    __tablename__ = "itinerary_items"
    # Deferrable, so a reorder can swap two positions inside one transaction. A plain UNIQUE fails the swap.
    __table_args__ = (
        UniqueConstraint("trip_id", "position", name="uq_itinerary_position", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id"))
    position: Mapped[int]
    # The provider's body as received (CONTRACT_NOTES 1.0, rule 4): never re-dumped from a model.
    offer: Mapped[dict] = mapped_column(JSONB)
