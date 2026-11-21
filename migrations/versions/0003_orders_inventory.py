"""Add stock quantities and idempotent order records."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_orders_inventory"
down_revision: Union[str, None] = "0002_catalog_listings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode is required because SQLite cannot directly add a CHECK constraint.
    with op.batch_alter_table("listings") as batch_op:
        batch_op.add_column(
            sa.Column("inventory_quantity", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_check_constraint(
            "ck_listings_inventory_nonnegative", "inventory_quantity >= 0"
        )

    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_payment"),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_orders_user_idempotency"),
        sa.CheckConstraint("total_minor > 0", name="ck_orders_total_positive"),
    )
    op.create_index("ix_orders_user_created", "orders", ["user_id", "created_at"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "order_id",
            sa.String(length=36),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "listing_id",
            sa.String(length=36),
            sa.ForeignKey("listings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("listing_organization_id", sa.String(length=36), nullable=False),
        sa.Column("title_snapshot", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        sa.CheckConstraint("unit_price_minor > 0", name="ck_order_items_price_positive"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")
    op.drop_index("ix_orders_user_created", table_name="orders")
    op.drop_table("orders")
    with op.batch_alter_table("listings") as batch_op:
        batch_op.drop_constraint("ck_listings_inventory_nonnegative", type_="check")
        batch_op.drop_column("inventory_quantity")
