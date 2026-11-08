import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, Numeric, Text, UniqueConstraint, func, text,
)
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


class Document(Base):
    """A trip document: the source captured when it was requested, and the file rendered from it (Iteration 5)."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("trip_id", "source_sha256", name="uq_documents_source"),
        CheckConstraint("state IN ('waiting','in_progress','complete','failed')", name="ck_documents_state"),
        CheckConstraint(
            "(state = 'complete') = (content IS NOT NULL AND content_sha256 IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_documents_complete",
        ),
        CheckConstraint("content IS NULL OR content_sha256 = encode(sha256(content), 'hex')", name="ck_documents_hash"),
        CheckConstraint(
            "state <> 'in_progress' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)", name="ck_documents_lease"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id"))
    trip_version: Mapped[int]
    source: Mapped[dict] = mapped_column(JSONB)
    source_sha256: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    lease_token: Mapped[uuid.UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    content_sha256: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
