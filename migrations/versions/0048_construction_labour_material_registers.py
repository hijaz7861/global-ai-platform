"""Add tenant-scoped append-only construction labour and material registers."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0048_construction_labour_mat"
down_revision: Union[str, None] = "0047_workforce_message_appeals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "construction_labour_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("worker_name", sa.String(140), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("worker_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("days", sa.Numeric(8, 2), nullable=False, server_default="1"),
        sa.Column("daily_rate_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["construction_projects.id", "construction_projects.organization_id"],
            ondelete="RESTRICT",
            name="fk_construction_labour_project_org",
        ),
        sa.CheckConstraint("length(trim(worker_name)) > 0", name="ck_construction_labour_worker_name"),
        sa.CheckConstraint("worker_count BETWEEN 1 AND 10000", name="ck_construction_labour_worker_count"),
        sa.CheckConstraint("days > 0 AND days <= 999999.99", name="ck_construction_labour_days"),
        sa.CheckConstraint(
            "daily_rate_minor BETWEEN 0 AND 9000000000000000",
            name="ck_construction_labour_daily_rate",
        ),
        sa.CheckConstraint(
            "total_amount_minor BETWEEN 0 AND 9000000000000000",
            name="ck_construction_labour_total",
        ),
        sa.CheckConstraint("currency = 'PKR'", name="ck_construction_labour_currency_pkr"),
    )
    op.create_index(
        "ix_construction_labour_org_project_date",
        "construction_labour_entries",
        ["organization_id", "project_id", "work_date"],
    )

    op.create_table(
        "construction_material_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("item_name", sa.String(160), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("supplier", sa.String(160), nullable=True),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["construction_projects.id", "construction_projects.organization_id"],
            ondelete="RESTRICT",
            name="fk_construction_material_project_org",
        ),
        sa.CheckConstraint("length(trim(item_name)) > 0", name="ck_construction_material_item_name"),
        sa.CheckConstraint("length(trim(unit)) > 0", name="ck_construction_material_unit"),
        sa.CheckConstraint("quantity > 0", name="ck_construction_material_quantity"),
        sa.CheckConstraint(
            "unit_price_minor BETWEEN 0 AND 9000000000000000",
            name="ck_construction_material_unit_price",
        ),
        sa.CheckConstraint(
            "total_amount_minor BETWEEN 0 AND 9000000000000000",
            name="ck_construction_material_total",
        ),
        sa.CheckConstraint("currency = 'PKR'", name="ck_construction_material_currency_pkr"),
    )
    op.create_index(
        "ix_construction_material_org_project_date",
        "construction_material_entries",
        ["organization_id", "project_id", "entry_date"],
    )

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_construction_register_mutation() RETURNS trigger AS $function$
            BEGIN
                RAISE EXCEPTION '% are append-only', TG_TABLE_NAME;
            END;
            $function$ LANGUAGE plpgsql
            """
        )
        for table, stem in (
            ("construction_labour_entries", "construction_labour"),
            ("construction_material_entries", "construction_material"),
        ):
            op.execute(
                f"""
                CREATE TRIGGER trg_{stem}_no_update
                BEFORE UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION reject_construction_register_mutation()
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER trg_{stem}_no_delete
                BEFORE DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION reject_construction_register_mutation()
                """
            )
    elif dialect == "sqlite":
        for table, stem in (
            ("construction_labour_entries", "construction_labour"),
            ("construction_material_entries", "construction_material"),
        ):
            op.execute(
                f"""
                CREATE TRIGGER trg_{stem}_no_update
                BEFORE UPDATE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{table} are append-only');
                END
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER trg_{stem}_no_delete
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{table} are append-only');
                END
                """
            )


def downgrade() -> None:
    connection = op.get_bind()
    labour_count = connection.execute(
        sa.text("SELECT count(*) FROM construction_labour_entries")
    ).scalar_one()
    material_count = connection.execute(
        sa.text("SELECT count(*) FROM construction_material_entries")
    ).scalar_one()
    if labour_count or material_count:
        raise RuntimeError(
            "Cannot downgrade while construction labour or material register entries exist"
        )

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table, stem in (
            ("construction_labour_entries", "construction_labour"),
            ("construction_material_entries", "construction_material"),
        ):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{stem}_no_update ON {table}")
            op.execute(f"DROP TRIGGER IF EXISTS trg_{stem}_no_delete ON {table}")
        op.execute("DROP FUNCTION IF EXISTS reject_construction_register_mutation()")
    elif dialect == "sqlite":
        for stem in ("construction_labour", "construction_material"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{stem}_no_update")
            op.execute(f"DROP TRIGGER IF EXISTS trg_{stem}_no_delete")

    op.drop_index("ix_construction_material_org_project_date", table_name="construction_material_entries")
    op.drop_table("construction_material_entries")
    op.drop_index("ix_construction_labour_org_project_date", table_name="construction_labour_entries")
    op.drop_table("construction_labour_entries")
