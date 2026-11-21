"""Add server-side access-token revocation records."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_token_revocation"
down_revision: Union[str, None] = "0005_double_entry_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "token_revocations",
        sa.Column("jti", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_token_revocations_expires_at", "token_revocations", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_token_revocations_expires_at", table_name="token_revocations")
    op.drop_table("token_revocations")
