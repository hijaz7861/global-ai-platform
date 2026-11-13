"""Add shared PostgreSQL nonce persistence for signed approvals."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0034_postgres_approval_nonces"
down_revision: Union[str, None] = "0033_ai_task_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consumed_approval_nonces",
        sa.Column("nonce", sa.String(length=128), primary_key=True),
        sa.Column("consumed_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_consumed_approval_nonces_time",
        "consumed_approval_nonces",
        ["consumed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_consumed_approval_nonces_time",
        table_name="consumed_approval_nonces",
    )
    op.drop_table("consumed_approval_nonces")
