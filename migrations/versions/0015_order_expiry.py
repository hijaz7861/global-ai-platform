"""Add expiry timestamps to unpaid orders."""
from datetime import timedelta
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015_order_expiry"
down_revision: Union[str, None] = "0014_refresh_token_rotation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    orders = sa.table(
        "orders",
        sa.column("id", sa.String(length=36)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("expires_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    for order_id, created_at in bind.execute(sa.select(orders.c.id, orders.c.created_at)):
        bind.execute(
            orders.update()
            .where(orders.c.id == order_id)
            .values(expires_at=created_at + timedelta(minutes=30))
        )
    with op.batch_alter_table("orders") as batch_op:
        batch_op.alter_column(
            "expires_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
    op.create_index("ix_orders_status_expires", "orders", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_orders_status_expires", table_name="orders")
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("expires_at")
