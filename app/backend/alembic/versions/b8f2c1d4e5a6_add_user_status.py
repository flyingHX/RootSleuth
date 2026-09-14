"""add user status column

Revision ID: b8f2c1d4e5a6
Revises: a575e0b9f029
Create Date: 2026-09-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8f2c1d4e5a6'
down_revision: Union[str, Sequence[str], None] = 'a575e0b9f029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """users 表新增 status 列（active/disabled），存量账号默认 active。"""
    op.add_column(
        'users',
        sa.Column('status', sa.String(length=50), nullable=False, server_default='active'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'status')
