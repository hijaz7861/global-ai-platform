"""Add encrypted TOTP MFA support to users."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017_totp_mfa"
down_revision: Union[str, None] = "0016_supplier_procurement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("users", sa.Column("mfa_secret_encrypted", sa.String(length=256), nullable=True))
    op.add_column("users", sa.Column("mfa_pending_secret_encrypted", sa.String(length=256), nullable=True))
    op.add_column("users", sa.Column("mfa_pending_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("mfa_last_verified_step", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "mfa_last_verified_step")
    op.drop_column("users", "mfa_pending_expires_at")
    op.drop_column("users", "mfa_pending_secret_encrypted")
    op.drop_column("users", "mfa_secret_encrypted")
    op.drop_column("users", "mfa_enabled")
