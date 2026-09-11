"""add pull_requests

Revision ID: a4b8c1d7e903
Revises: e7f3a1c9d2b4
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a4b8c1d7e903'
down_revision: Union[str, Sequence[str], None] = 'e7f3a1c9d2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    docs/phase4.md: persist each branch/commit/draft-PR delivery for a
    validated fix. Enum labels are uppercase member *names*, matching this
    project's existing Enum columns (see the initial migration).
    """
    # create_type=False: the types are created explicitly below, so
    # create_table must not emit a second CREATE TYPE for the same names.
    delivery_status = postgresql.ENUM(
        'PENDING', 'COMMITTED', 'PR_CREATED', 'FAILED',
        name='delivery_status', create_type=False,
    )
    delivery_failure_type = postgresql.ENUM(
        'NONE',
        'NOT_VALIDATED',
        'PATCH_APPLICATION_ERROR',
        'VERIFICATION_FAILED',
        'GIT_ERROR',
        'GITHUB_AUTH_ERROR',
        'GITHUB_PERMISSION_ERROR',
        'BRANCH_CONFLICT',
        'PR_CREATION_ERROR',
        name='delivery_failure_type',
        create_type=False,
    )
    delivery_status.create(op.get_bind(), checkfirst=True)
    delivery_failure_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'pull_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('attempt_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('repo', sa.String(), nullable=False),
        sa.Column('base_commit', sa.String(), nullable=False),
        sa.Column('branch_name', sa.String(), nullable=False),
        sa.Column('commit_sha', sa.String(), nullable=True),
        sa.Column('commit_message', sa.Text(), nullable=True),
        sa.Column('diff', sa.Text(), nullable=True),
        sa.Column('status', delivery_status, nullable=False),
        sa.Column('failure_type', delivery_failure_type, nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('target_repo', sa.String(), nullable=True),
        sa.Column('base_branch', sa.String(), nullable=True),
        sa.Column('pr_url', sa.String(), nullable=True),
        sa.Column('pr_number', sa.Integer(), nullable=True),
        sa.Column('pr_state', sa.String(), nullable=True),
        sa.Column('is_draft', sa.Boolean(), nullable=False),
        sa.Column('pushed', sa.Boolean(), nullable=False),
        sa.Column('verification_command', sa.Text(), nullable=True),
        sa.Column('verification_passed', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('committed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pr_created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['attempt_id'], ['attempts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pull_requests_run_id'), 'pull_requests', ['run_id'])
    op.create_index(op.f('ix_pull_requests_attempt_id'), 'pull_requests', ['attempt_id'])
    op.create_index(op.f('ix_pull_requests_status'), 'pull_requests', ['status'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_pull_requests_status'), table_name='pull_requests')
    op.drop_index(op.f('ix_pull_requests_attempt_id'), table_name='pull_requests')
    op.drop_index(op.f('ix_pull_requests_run_id'), table_name='pull_requests')
    op.drop_table('pull_requests')
    postgresql.ENUM(name='delivery_failure_type').drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='delivery_status').drop(op.get_bind(), checkfirst=True)
