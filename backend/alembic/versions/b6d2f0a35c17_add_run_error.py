"""add error to runs

Revision ID: b6d2f0a35c17
Revises: a4b8c1d7e903
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b6d2f0a35c17'
down_revision: Union[str, Sequence[str], None] = 'a4b8c1d7e903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    docs/phase5.md "Reliability": a failed run has to say why, both for an
    operator and for the dashboard that renders it.
    """
    op.add_column('runs', sa.Column('error', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('runs', 'error')
