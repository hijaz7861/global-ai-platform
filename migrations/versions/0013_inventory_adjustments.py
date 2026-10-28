"""Add append-only inventory adjustment history."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013_inventory_adjustments"
down_revision: Union[str, None] = "0012_accounting_periods"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_adjustments",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("listing_id", sa.String(length=36), sa.ForeignKey("listings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("quantity_before", sa.Integer(), nullable=False),
        sa.Column("quantity_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("delta != 0", name="ck_inventory_adjustments_nonzero_delta"),
        sa.CheckConstraint("quantity_before >= 0 AND quantity_after >= 0", name="ck_inventory_adjustments_nonnegative_quantities"),
    )
    op.create_index("ix_inventory_adjustments_listing_created", "inventory_adjustments", ["listing_id", "created_at"])
    op.create_index("ix_inventory_adjustments_org_created", "inventory_adjustments", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_inventory_adjustments_org_created", table_name="inventory_adjustments")
    op.drop_index("ix_inventory_adjustments_listing_created", table_name="inventory_adjustments")
    op.drop_table("inventory_adjustments")
