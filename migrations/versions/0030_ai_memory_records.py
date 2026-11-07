"""Add provenance-aware user and organization AI memory records."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0030_ai_memory_records"
down_revision: Union[str, None] = "0029_ai_task_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_memory_records",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("owner_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("memory_type", sa.String(length=24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=True),
        sa.Column("confidence_percent", sa.Integer(), server_default="50", nullable=False),
        sa.Column("sensitivity", sa.String(length=16), server_default="normal", nullable=False),
        sa.Column("verification_status", sa.String(length=16), server_default="unverified", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("supersedes_id", sa.String(length=36), sa.ForeignKey("ai_memory_records.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope = 'user' AND owner_user_id IS NOT NULL AND organization_id IS NULL) OR "
            "(scope = 'organization' AND organization_id IS NOT NULL AND owner_user_id IS NULL)",
            name="ck_ai_memory_scope_owner",
        ),
        sa.CheckConstraint("scope IN ('user','organization')", name="ck_ai_memory_scope"),
        sa.CheckConstraint(
            "memory_type IN ('preference','feedback','project','reference','decision','session_summary')",
            name="ck_ai_memory_type",
        ),
        sa.CheckConstraint("sensitivity IN ('normal','sensitive','restricted')", name="ck_ai_memory_sensitivity"),
        sa.CheckConstraint(
            "verification_status IN ('unverified','verified','superseded')",
            name="ck_ai_memory_verification",
        ),
        sa.CheckConstraint("status IN ('active','archived','superseded')", name="ck_ai_memory_status"),
        sa.CheckConstraint("confidence_percent BETWEEN 0 AND 100", name="ck_ai_memory_confidence"),
    )
    op.create_index(
        "ix_ai_memory_org_scope_status",
        "ai_memory_records",
        ["organization_id", "scope", "status", "updated_at"],
    )
    op.create_index(
        "ix_ai_memory_owner_scope_status",
        "ai_memory_records",
        ["owner_user_id", "scope", "status", "updated_at"],
    )
    op.create_index(
        "ix_ai_memory_type_created",
        "ai_memory_records",
        ["memory_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_memory_type_created", table_name="ai_memory_records")
    op.drop_index("ix_ai_memory_owner_scope_status", table_name="ai_memory_records")
    op.drop_index("ix_ai_memory_org_scope_status", table_name="ai_memory_records")
    op.drop_table("ai_memory_records")
