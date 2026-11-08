"""itinerary items and the trip party size

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07

Forward-only: there is no downgrade. Existing trips get 1 traveler, and the column keeps no default,
so every new trip states its party.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('trips', sa.Column('travelers', sa.Integer(), server_default='1', nullable=False))
    op.alter_column('trips', 'travelers', server_default=None)
    op.create_check_constraint('ck_trips_travelers', 'trips', 'travelers BETWEEN 1 AND 20')
    op.create_table('itinerary_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('trip_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('offer', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.ForeignKeyConstraint(['trip_id'], ['trips.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('trip_id', 'position', name='uq_itinerary_position', deferrable=True, initially='DEFERRED')
    )
