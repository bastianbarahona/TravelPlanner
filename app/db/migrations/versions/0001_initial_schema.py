"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-07

Forward-only: there is no downgrade.
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('trips',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('starts_on', sa.Date(), nullable=False),
    sa.Column('ends_on', sa.Date(), nullable=False),
    sa.Column('budget_usd', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint('budget_usd >= 0', name='ck_trips_budget'),
    sa.CheckConstraint('ends_on >= starts_on', name='ck_trips_dates'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('trip_destinations',
    sa.Column('trip_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['trip_id'], ['trips.id'], ),
    sa.PrimaryKeyConstraint('trip_id', 'position')
    )
    op.create_table('trip_participants',
    sa.Column('trip_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.CheckConstraint("role IN ('organizer','editor','viewer')", name='ck_participants_role'),
    sa.ForeignKeyConstraint(['trip_id'], ['trips.id'], ),
    sa.PrimaryKeyConstraint('trip_id', 'user_id')
    )
    op.create_index('uq_one_organizer', 'trip_participants', ['trip_id'], unique=True, postgresql_where=sa.text("role = 'organizer'"))
