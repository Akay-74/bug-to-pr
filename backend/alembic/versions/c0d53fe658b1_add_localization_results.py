"""add localization_results

Revision ID: c0d53fe658b1
Revises: bd09fd2e2d05
Create Date: 2026-08-30 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c0d53fe658b1'
down_revision: Union[str, Sequence[str], None] = 'bd09fd2e2d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'localization_results',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('issue_id', sa.String(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('file', sa.String(), nullable=False),
        sa.Column('symbol', sa.String(), nullable=False),
        sa.Column('symbol_type', sa.String(), nullable=False),
        sa.Column('start_line', sa.Integer(), nullable=False),
        sa.Column('end_line', sa.Integer(), nullable=False),
        sa.Column('semantic_score', sa.Float(), nullable=False),
        sa.Column('lexical_score', sa.Float(), nullable=False),
        sa.Column('relevance_score', sa.Float(), nullable=False),
        sa.Column('final_score', sa.Float(), nullable=False),
        sa.Column('matched_terms', postgresql.JSON(), nullable=False),
        sa.Column('model_identifier', sa.String(), nullable=False),
        sa.Column('index_identifier', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_localization_results_issue_id'), 'localization_results', ['issue_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_localization_results_issue_id'), table_name='localization_results')
    op.drop_table('localization_results')
