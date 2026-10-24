"""Add verified email ownership and single-use verification tokens."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_email_verification"
down_revision: Union[str, None] = "0009_auth_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing accounts predate this feature and remain usable; new ORM-created
    # accounts explicitly set email_verified=False.
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_email_verification_user_expiry",
        "email_verification_tokens",
        ["user_id", "expires_at"],
    )
    op.create_index(
        "ix_email_verification_expiry",
        "email_verification_tokens",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_verification_expiry", table_name="email_verification_tokens")
    op.drop_index("ix_email_verification_user_expiry", table_name="email_verification_tokens")
    op.drop_table("email_verification_tokens")
    op.drop_column("users", "email_verified")
