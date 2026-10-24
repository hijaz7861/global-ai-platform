"""Add persistent rate-limit attempts for authentication endpoints."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_auth_attempts"
down_revision: Union[str, None] = "0008_supplier_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_auth_attempt_scope_subject_created",
        "auth_attempts",
        ["scope", "subject_hash", "created_at"],
    )
    op.create_index("ix_auth_attempt_created", "auth_attempts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_attempt_created", table_name="auth_attempts")
    op.drop_index("ix_auth_attempt_scope_subject_created", table_name="auth_attempts")
    op.drop_table("auth_attempts")
