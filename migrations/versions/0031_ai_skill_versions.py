"""Add versioned organization-scoped AI skills."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0031_ai_skill_versions"
down_revision: Union[str, None] = "0030_ai_memory_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_skill_versions",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=True),
        sa.Column("license_id", sa.String(length=64), nullable=False),
        sa.Column("required_capabilities", sa.JSON(), nullable=False),
        sa.Column("data_classes", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=16), server_default="low", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approved_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "skill_id", "version", name="uq_ai_skill_org_id_version"),
        sa.CheckConstraint("status IN ('draft','approved','disabled','deprecated')", name="ck_ai_skill_status"),
        sa.CheckConstraint("risk_level IN ('low','medium','high','critical')", name="ck_ai_skill_risk"),
        sa.CheckConstraint("source_type IN ('internal','user_authored','imported')", name="ck_ai_skill_source_type"),
    )
    op.create_index("ix_ai_skill_org_status", "ai_skill_versions", ["organization_id", "status", "skill_id"])
    op.create_index("ix_ai_skill_org_created", "ai_skill_versions", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_skill_org_created", table_name="ai_skill_versions")
    op.drop_index("ix_ai_skill_org_status", table_name="ai_skill_versions")
    op.drop_table("ai_skill_versions")
