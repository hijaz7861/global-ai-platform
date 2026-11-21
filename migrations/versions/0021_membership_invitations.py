"""Add expiring organization membership invitations."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0021_membership_invitations"
down_revision: Union[str, None] = "0020_account_lockout"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "membership_invitations",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_membership_invites_org_email", "membership_invitations", ["organization_id", "email"])
    op.create_index("ix_membership_invites_expiry", "membership_invitations", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_membership_invites_expiry", table_name="membership_invitations")
    op.drop_index("ix_membership_invites_org_email", table_name="membership_invitations")
    op.drop_table("membership_invitations")
