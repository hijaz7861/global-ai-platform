"""Add purchase order issuance and supplier fulfilment."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022_purchase_orders"
down_revision: Union[str, None] = "0021_membership_invitations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("procurement_request_id", sa.String(length=36), sa.ForeignKey("procurement_requests.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("supplier_quote_id", sa.String(length=36), sa.ForeignKey("supplier_quotes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("buyer_organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("supplier_organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="issued", nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_purchase_orders_quantity_positive"),
        sa.CheckConstraint("unit_price_minor > 0", name="ck_purchase_orders_unit_price_positive"),
        sa.CheckConstraint("total_minor > 0", name="ck_purchase_orders_total_positive"),
        sa.CheckConstraint("status IN ('issued','fulfilled','cancelled')", name="ck_purchase_orders_status"),
        sa.UniqueConstraint("procurement_request_id", name="uq_purchase_order_request"),
        sa.UniqueConstraint("supplier_quote_id", name="uq_purchase_order_quote"),
    )
    op.create_index("ix_purchase_orders_buyer_created", "purchase_orders", ["buyer_organization_id", "created_at"])
    op.create_index("ix_purchase_orders_supplier_status", "purchase_orders", ["supplier_organization_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_purchase_orders_supplier_status", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_buyer_created", table_name="purchase_orders")
    op.drop_table("purchase_orders")
