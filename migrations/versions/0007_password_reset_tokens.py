"""Add password reset tokens and authentication-version invalidation."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
revision: str = "0007_password_reset_tokens"
down_revision: Union[str, None] = "0006_token_revocation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("users", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("password_reset_tokens", sa.Column("id", sa.String(length=36), primary_key=True, nullable=False), sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("used_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_password_reset_user_expiry", "password_reset_tokens", ["user_id", "expires_at"])
    op.create_index("ix_password_reset_expiry", "password_reset_tokens", ["expires_at"])

def downgrade() -> None:
    op.drop_index("ix_password_reset_expiry", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_user_expiry", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_column("users", "auth_version")
