"""Add one-time MFA recovery codes."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0019_mfa_recovery_codes"
down_revision: Union[str, None] = "0018_auth_rate_limit_locks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("code_hash", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mfa_recovery_codes_user_used", "mfa_recovery_codes", ["user_id", "used_at"])
    op.create_index("ix_mfa_recovery_codes_created", "mfa_recovery_codes", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mfa_recovery_codes_created", table_name="mfa_recovery_codes")
    op.drop_index("ix_mfa_recovery_codes_user_used", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")
