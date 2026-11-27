"""Add encrypted durable checkpoints for resumable agent tasks."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0050_ai_agent_checkpoints"
down_revision: Union[str, None] = "0049_customer_engagement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_checkpoints",
        sa.Column("id", sa.String(length=36), primary_key=True),
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
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("state_ciphertext", sa.Text(), nullable=False),
        sa.Column("state_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_id", name="uq_ai_agent_checkpoint_task"),
        sa.CheckConstraint("revision > 0", name="ck_ai_agent_checkpoint_revision"),
        sa.CheckConstraint("turn_number BETWEEN 1 AND 5", name="ck_ai_agent_checkpoint_turn"),
        sa.CheckConstraint(
            "phase IN ('ready','model_call_started','decision_saved','tool_started','final_saved')",
            name="ck_ai_agent_checkpoint_phase",
        ),
    )
    op.create_index(
        "ix_ai_agent_checkpoint_org_updated",
        "ai_agent_checkpoints",
        ["organization_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_agent_checkpoint_org_updated", table_name="ai_agent_checkpoints")
    op.drop_table("ai_agent_checkpoints")
