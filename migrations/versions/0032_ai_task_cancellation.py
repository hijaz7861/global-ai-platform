"""Add cooperative cancellation requests to durable AI tasks."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0032_ai_task_cancellation"
down_revision: Union[str, None] = "0031_ai_skill_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_tasks") as batch_op:
        batch_op.add_column(sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("ai_task_events") as batch_op:
        batch_op.drop_constraint("ck_ai_task_events_type", type_="check")
        batch_op.create_check_constraint(
            "ck_ai_task_events_type",
            "event_type IN ('created','claimed','heartbeat','completed','retry_scheduled','failed','cancel_requested','cancelled','lease_expired')",
        )


def downgrade() -> None:
    # Keep the expanded event check so existing cancellation events remain valid
    # if the application is rolled back before the data is migrated elsewhere.
    with op.batch_alter_table("ai_tasks") as batch_op:
        batch_op.drop_column("cancel_requested_at")
