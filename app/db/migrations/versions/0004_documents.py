"""documents

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07

Forward-only: there is no downgrade.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('documents',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('trip_id', sa.Uuid(), nullable=False),
    sa.Column('trip_version', sa.Integer(), nullable=False),
    sa.Column('source', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('source_sha256', sa.Text(), nullable=False),
    sa.Column('state', sa.Text(), nullable=False),
    sa.Column('requested_by', sa.Text(), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('lease_token', sa.Uuid(), nullable=True),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('content', sa.LargeBinary(), nullable=True),
    sa.Column('content_sha256', sa.Text(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.CheckConstraint("state IN ('waiting','in_progress','complete','failed')", name='ck_documents_state'),
    sa.CheckConstraint("(state = 'complete') = (content IS NOT NULL AND content_sha256 IS NOT NULL AND completed_at IS NOT NULL)", name='ck_documents_complete'),
    sa.CheckConstraint("content IS NULL OR content_sha256 = encode(sha256(content), 'hex')", name='ck_documents_hash'),
    sa.CheckConstraint("state <> 'in_progress' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)", name='ck_documents_lease'),
    sa.ForeignKeyConstraint(['trip_id'], ['trips.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('trip_id', 'source_sha256', name='uq_documents_source')
    )
