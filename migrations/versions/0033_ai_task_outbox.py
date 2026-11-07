"""Add transactional outbox for AI task lifecycle events."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0033_ai_task_outbox"
down_revision: Union[str, None] = "0032_ai_task_cancellation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_outbox_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "task_event_id",
            sa.String(length=36),
            sa.ForeignKey("ai_task_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("ai_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_event_id", name="uq_ai_outbox_task_event"),
        sa.CheckConstraint(
            "status IN ('pending','processing','delivered','dead')",
            name="ck_ai_outbox_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 20",
            name="ck_ai_outbox_attempts",
        ),
    )
    op.create_index(
        "ix_ai_outbox_dispatch", "ai_outbox_events",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_ai_outbox_org_created", "ai_outbox_events",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_outbox_org_created", table_name="ai_outbox_events")
    op.drop_index("ix_ai_outbox_dispatch", table_name="ai_outbox_events")
    op.drop_table("ai_outbox_events")
