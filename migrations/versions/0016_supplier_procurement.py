"""Add procurement requests and supplier quote workflow."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016_supplier_procurement"
down_revision: Union[str, None] = "0015_order_expiry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "procurement_requests",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("buyer_organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=3000), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("awarded_quote_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_procurement_requests_quantity_positive"),
        sa.CheckConstraint("length(currency) = 3", name="ck_procurement_requests_currency_length"),
        sa.CheckConstraint("status IN ('open','awarded','closed')", name="ck_procurement_requests_status"),
    )
    op.create_index(
        "ix_procurement_requests_org_status_created",
        "procurement_requests",
        ["buyer_organization_id", "status", "created_at"],
    )
    op.create_table(
        "supplier_quotes",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("procurement_request_id", sa.String(length=36), sa.ForeignKey("procurement_requests.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("supplier_organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("procurement_request_id", "supplier_organization_id", name="uq_supplier_quote_request_supplier"),
        sa.CheckConstraint("unit_price_minor > 0", name="ck_supplier_quotes_price_positive"),
        sa.CheckConstraint("lead_time_days >= 0", name="ck_supplier_quotes_lead_time_nonnegative"),
        sa.CheckConstraint("status IN ('submitted','accepted','rejected','withdrawn')", name="ck_supplier_quotes_status"),
    )
    op.create_index("ix_supplier_quotes_request_status", "supplier_quotes", ["procurement_request_id", "status"])
    op.create_index("ix_supplier_quotes_supplier_created", "supplier_quotes", ["supplier_organization_id", "created_at"])
    with op.batch_alter_table("procurement_requests") as batch_op:
        batch_op.create_foreign_key(
            "fk_procurement_requests_awarded_quote_id_supplier_quotes",
            "supplier_quotes",
            ["awarded_quote_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("procurement_requests") as batch_op:
        batch_op.drop_constraint(
            "fk_procurement_requests_awarded_quote_id_supplier_quotes",
            type_="foreignkey",
        )
    op.drop_index("ix_supplier_quotes_supplier_created", table_name="supplier_quotes")
    op.drop_index("ix_supplier_quotes_request_status", table_name="supplier_quotes")
    op.drop_table("supplier_quotes")
    op.drop_index("ix_procurement_requests_org_status_created", table_name="procurement_requests")
    op.drop_table("procurement_requests")
