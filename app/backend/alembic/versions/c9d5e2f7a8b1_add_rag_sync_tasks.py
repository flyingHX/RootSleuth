"""add rag_sync_tasks compensation queue

Revision ID: c9d5e2f7a8b1
Revises: b8f2c1d4e5a6
Create Date: 2026-09-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d5e2f7a8b1'
down_revision: Union[str, Sequence[str], None] = 'b8f2c1d4e5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rag_sync_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('idempotency_key', sa.String(length=191), nullable=False),
        sa.Column('task_type', sa.String(length=32), nullable=False),
        sa.Column('case_id', sa.String(length=128), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('last_http_status', sa.Integer(), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('verify_detail', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(length=191), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_rag_sync_tasks_idempotency_key', 'rag_sync_tasks', ['idempotency_key'], unique=True
    )
    op.create_index('ix_rag_sync_tasks_status', 'rag_sync_tasks', ['status'])
    op.create_index('ix_rag_sync_tasks_case_id', 'rag_sync_tasks', ['case_id'])
    op.create_index('ix_rag_sync_tasks_next_retry_at', 'rag_sync_tasks', ['next_retry_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_rag_sync_tasks_next_retry_at', table_name='rag_sync_tasks')
    op.drop_index('ix_rag_sync_tasks_case_id', table_name='rag_sync_tasks')
    op.drop_index('ix_rag_sync_tasks_status', table_name='rag_sync_tasks')
    op.drop_index('ix_rag_sync_tasks_idempotency_key', table_name='rag_sync_tasks')
    op.drop_table('rag_sync_tasks')
