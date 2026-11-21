"""Add durable tenant-scoped AI task runtime tables."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0029_ai_task_runtime"
down_revision: Union[str, None] = "0028_reconciliation_imports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "created_by_user_id", "idempotency_key", name="uq_ai_task_actor_idempotency"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled')", name="ck_ai_tasks_status"),
        sa.CheckConstraint("attempt_count >= 0 AND max_attempts BETWEEN 1 AND 10", name="ck_ai_tasks_attempts"),
    )
    op.create_index("ix_ai_tasks_claim", "ai_tasks", ["status", "available_at", "created_at"])
    op.create_index("ix_ai_tasks_org_created", "ai_tasks", ["organization_id", "created_at"])

    op.create_table(
        "ai_task_steps",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("ai_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="running", nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "sequence_number", name="uq_ai_task_step_sequence"),
        sa.CheckConstraint("status IN ('running','succeeded','failed','abandoned')", name="ck_ai_task_steps_status"),
        sa.CheckConstraint("sequence_number > 0", name="ck_ai_task_steps_sequence_positive"),
    )
    op.create_index("ix_ai_task_steps_org_created", "ai_task_steps", ["organization_id", "created_at"])

    op.create_table(
        "ai_task_events",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("ai_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("event_type IN ('created','claimed','heartbeat','completed','retry_scheduled','failed','cancelled','lease_expired')", name="ck_ai_task_events_type"),
    )
    op.create_index("ix_ai_task_events_org_created", "ai_task_events", ["organization_id", "created_at"])
    op.create_index("ix_ai_task_events_task_created", "ai_task_events", ["task_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_task_events_task_created", table_name="ai_task_events")
    op.drop_index("ix_ai_task_events_org_created", table_name="ai_task_events")
    op.drop_table("ai_task_events")
    op.drop_index("ix_ai_task_steps_org_created", table_name="ai_task_steps")
    op.drop_table("ai_task_steps")
    op.drop_index("ix_ai_tasks_org_created", table_name="ai_tasks")
    op.drop_index("ix_ai_tasks_claim", table_name="ai_tasks")
    op.drop_table("ai_tasks")
