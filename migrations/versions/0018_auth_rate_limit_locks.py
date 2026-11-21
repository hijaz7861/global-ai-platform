"""Add per-subject auth rate-limit serialization locks."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0018_auth_rate_limit_locks"
down_revision: Union[str, None] = "0017_totp_mfa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_rate_limit_locks",
        sa.Column("scope", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("subject_hash", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("touched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_rate_limit_locks_touched", "auth_rate_limit_locks", ["touched_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_rate_limit_locks_touched", table_name="auth_rate_limit_locks")
    op.drop_table("auth_rate_limit_locks")
