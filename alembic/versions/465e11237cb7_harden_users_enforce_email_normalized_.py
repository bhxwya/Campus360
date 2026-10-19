"""harden users: enforce email_normalized = lower(email)

Revision ID: 465e11237cb7
Revises: b9ebf3f265a4
Create Date: 2026-10-20 02:00:25.400069

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '465e11237cb7'
down_revision: Union[str, Sequence[str], None] = 'b9ebf3f265a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    NOTE: Alembic autogenerate does NOT detect CHECK-constraint changes, so it
    produced an empty body here. The DDL is written by hand (the naming
    convention renders the name as ck_users_email_normalized_lowercase).
    Adding a NOT VALID->VALID style two-step is unnecessary because Stage 2.1
    verified the development table has 0 violating rows first, so the
    constraint can be created and validated in one safe step.
    """
    op.create_check_constraint(
        "email_normalized_lowercase",
        table_name="users",
        condition="email_normalized = lower(email)",
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the hardening CHECK, restoring the previous behaviour where a
    mismatched email_normalized was allowed. No data is touched.
    """
    op.drop_constraint(
        "ck_users_email_normalized_lowercase",
        table_name="users",
        type_="check",
    )
