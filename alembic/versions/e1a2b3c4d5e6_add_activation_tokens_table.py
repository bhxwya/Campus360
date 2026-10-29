"""add activation_tokens table for secure account activation

Revision ID: e1a2b3c4d5e6
Revises: 9d2e1ea7b2d9
Create Date: 2026-10-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = '9d2e1ea7b2d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the activation_tokens table.

    Each row is a single-use, time-bound activation token bound to one user.
    Only the SHA-256 digest is ever persisted; the raw token is delivered to
    the student by email and never written to this table, any log, or any
    CSV export.
    """
    op.create_table(
        'activation_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column(
            'user_id',
            sa.Integer(),
            nullable=False,
            index=True,  # "list this account's pending activations" / revoke on resend
        ),
        sa.Column(
            'token_hash',
            sa.String(length=64),
            nullable=False,
            unique=True,
            index=True,  # lookup by digest when the student submits the raw token
        ),
        sa.Column(
            'expires_at',
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,  # supports "delete expired tokens" cleanup sweeps
        ),
        sa.Column(
            'consumed_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_activation_tokens_user_id_users'),
            ondelete='CASCADE',  # deleting an account removes its pending activations
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_activation_tokens')),
    )


def downgrade() -> None:
    """Remove the activation_tokens table (reversible)."""
    op.drop_table('activation_tokens')
