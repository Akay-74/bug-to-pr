"""add patch_application_error to attempt_failure_type

Revision ID: e7f3a1c9d2b4
Revises: c0d53fe658b1
Create Date: 2026-09-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7f3a1c9d2b4'
down_revision: Union[str, Sequence[str], None] = 'c0d53fe658b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    docs/phase3.md §4 requires distinguishing patch-application failure from
    generation/environment/test failure. PostgreSQL requires ALTER TYPE ...
    ADD VALUE to run outside a transaction block.
    """
    # NB: this project's Enum columns store the Python Enum member's *name*
    # (SQLAlchemy's default for a plain Enum(SomeEnum, name=...)), not its
    # .value -- see the original migration's uppercase labels
    # ('GENERATION_ERROR', etc). This label must match that convention.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE attempt_failure_type ADD VALUE IF NOT EXISTS 'PATCH_APPLICATION_ERROR'")


def downgrade() -> None:
    """Downgrade schema.

    PostgreSQL cannot drop a single enum value in place; downgrading this
    enum requires rebuilding the type, which is not needed for this project.
    """
    pass
