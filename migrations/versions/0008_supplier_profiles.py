"""Add organization-scoped supplier onboarding and review state."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_supplier_profiles"
down_revision: Union[str, None] = "0007_password_reset_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "supplier_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("business_type", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("contact_email", sa.String(length=320), nullable=False),
        sa.Column("website_url", sa.String(length=2048), nullable=True),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewer_notes", sa.String(length=2000), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewed_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", name="uq_supplier_profiles_organization"),
        sa.CheckConstraint("status IN ('draft','submitted','approved','rejected')", name="ck_supplier_profiles_status"),
        sa.CheckConstraint("length(country_code) = 2", name="ck_supplier_profiles_country_code"),
    )
    op.create_index("ix_supplier_profiles_status_created", "supplier_profiles", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_supplier_profiles_status_created", table_name="supplier_profiles")
    op.drop_table("supplier_profiles")
