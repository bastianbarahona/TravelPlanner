"""booking attempts, and one item per offer

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07

Forward-only: there is no downgrade. uq_itinerary_offer fails on a trip that already holds the same offer twice
(Iteration 3 allowed it): resolve those rows by hand first. Nothing was deployed before this revision.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('uq_itinerary_offer', 'itinerary_items', ['trip_id', sa.text("(offer->>'id')")], unique=True)
    op.create_table('booking_attempts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('trip_id', sa.Uuid(), nullable=False),
    sa.Column('item_id', sa.Uuid(), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('idempotency_key', sa.Text(), nullable=False),
    sa.Column('client_reference', sa.Text(), nullable=False),
    sa.Column('request', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state', sa.Text(), nullable=False),
    sa.Column('booking', postgresql.JSONB(astext_type=sa.Text(), none_as_null=True), nullable=True),
    sa.Column('last_error', postgresql.JSONB(astext_type=sa.Text(), none_as_null=True), nullable=True),
    sa.Column('requested_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_check_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('check_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.CheckConstraint("state IN ('unknown','pending','confirmed','failed','rejected')", name='ck_booking_attempts_state'),
    sa.ForeignKeyConstraint(['item_id'], ['itinerary_items.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['trip_id'], ['trips.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('client_reference'),
    sa.UniqueConstraint('idempotency_key'),
    sa.UniqueConstraint('item_id', 'attempt_no', name='uq_booking_attempt_number')
    )
    op.create_index('uq_one_open_attempt', 'booking_attempts', ['item_id'], unique=True, postgresql_where=sa.text("state NOT IN ('rejected','failed')"))
