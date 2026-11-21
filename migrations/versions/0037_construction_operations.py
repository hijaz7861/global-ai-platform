"""Add tenant-scoped construction clients, projects, and BOQ lines."""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0037_construction_operations"
down_revision: Union[str, None] = "0036_transport_marketplace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "construction_clients",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("id", "organization_id", name="uq_construction_clients_id_org"),
    )
    op.create_index("ix_construction_clients_org_name", "construction_clients", ["organization_id", "display_name"])

    op.create_table(
        "construction_projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(180), nullable=False),
        sa.Column("work_type", sa.String(40), nullable=False),
        sa.Column("contract_type", sa.String(24), nullable=False),
        sa.Column("contract_amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("location_label", sa.String(200), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="lead"),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.String(1500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("id", "organization_id", name="uq_construction_projects_id_org"),
        sa.ForeignKeyConstraint(["client_id", "organization_id"],
            ["construction_clients.id", "construction_clients.organization_id"],
            ondelete="RESTRICT", name="fk_construction_projects_client_org"),
        sa.CheckConstraint("work_type IN ('grey_structure','superstructure','complete_construction','renovation','other')",
                           name="ck_construction_projects_work_type"),
        sa.CheckConstraint("contract_type IN ('labour_only','labour_material')",
                           name="ck_construction_projects_contract_type"),
        sa.CheckConstraint("contract_amount_minor >= 0", name="ck_construction_projects_amount_nonnegative"),
        sa.CheckConstraint("currency = 'PKR'", name="ck_construction_projects_currency_pkr"),
        sa.CheckConstraint("status IN ('lead','quoted','approved','in_progress','on_hold','completed','cancelled')",
                           name="ck_construction_projects_status"),
        sa.CheckConstraint("progress_percent BETWEEN 0 AND 100", name="ck_construction_projects_progress"),
    )
    op.create_index("ix_construction_projects_org_status_created", "construction_projects",
                    ["organization_id", "status", "created_at"])
    op.create_index("ix_construction_projects_client", "construction_projects", ["client_id"])

    op.create_table(
        "construction_boq_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("item_name", sa.String(160), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_rate_minor", sa.BigInteger(), nullable=False),
        sa.Column("line_total_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id", "organization_id"],
            ["construction_projects.id", "construction_projects.organization_id"],
            ondelete="RESTRICT", name="fk_construction_boq_project_org"),
        sa.UniqueConstraint("project_id", "line_number", name="uq_construction_boq_project_line"),
        sa.CheckConstraint("line_number BETWEEN 1 AND 10000", name="ck_construction_boq_line_number"),
        sa.CheckConstraint("quantity > 0", name="ck_construction_boq_quantity_positive"),
        sa.CheckConstraint("unit_rate_minor >= 0", name="ck_construction_boq_rate_nonnegative"),
        sa.CheckConstraint("line_total_minor >= 0", name="ck_construction_boq_total_nonnegative"),
    )
    op.create_index("ix_construction_boq_org_project", "construction_boq_items",
                    ["organization_id", "project_id", "line_number"])


def downgrade() -> None:
    op.drop_index("ix_construction_boq_org_project", table_name="construction_boq_items")
    op.drop_table("construction_boq_items")
    op.drop_index("ix_construction_projects_client", table_name="construction_projects")
    op.drop_index("ix_construction_projects_org_status_created", table_name="construction_projects")
    op.drop_table("construction_projects")
    op.drop_index("ix_construction_clients_org_name", table_name="construction_clients")
    op.drop_table("construction_clients")
