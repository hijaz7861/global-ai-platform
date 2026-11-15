"""Link construction cashbook events to immutable double-entry journal postings."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0039_construction_cashbook"
down_revision: Union[str, None] = "0038_construction_quotes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite and PostgreSQL both need a unique parent key for the composite
    # (journal_entry_id, organization_id) foreign key below.
    op.create_index(
        "uq_journal_entries_id_org",
        "journal_entries",
        ["id", "organization_id"],
        unique=True,
    )
    op.create_table(
        "construction_cashbook_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("journal_entry_id", sa.String(36), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["construction_projects.id", "construction_projects.organization_id"],
            ondelete="RESTRICT",
            name="fk_construction_cashbook_project_org",
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id", "organization_id"],
            ["journal_entries.id", "journal_entries.organization_id"],
            ondelete="RESTRICT",
            name="fk_construction_cashbook_journal_org",
        ),
        sa.UniqueConstraint("journal_entry_id", name="uq_construction_cashbook_journal_entry"),
        sa.CheckConstraint(
            "direction IN ('income','expense')",
            name="ck_construction_cashbook_direction",
        ),
        sa.CheckConstraint(
            "amount_minor > 0 AND amount_minor <= 9000000000000000",
            name="ck_construction_cashbook_amount",
        ),
        sa.CheckConstraint("currency = 'PKR'", name="ck_construction_cashbook_currency_pkr"),
    )
    op.create_index(
        "ix_construction_cashbook_org_project_created",
        "construction_cashbook_entries",
        ["organization_id", "project_id", "created_at"],
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_construction_cashbook_mutation() RETURNS trigger AS $
            BEGIN
                RAISE EXCEPTION 'construction_cashbook_entries are append-only';
            END;
            $ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_construction_cashbook_no_update
            BEFORE UPDATE ON construction_cashbook_entries
            FOR EACH ROW EXECUTE FUNCTION reject_construction_cashbook_mutation()
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_construction_cashbook_no_delete
            BEFORE DELETE ON construction_cashbook_entries
            FOR EACH ROW EXECUTE FUNCTION reject_construction_cashbook_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_construction_cashbook_no_update
            BEFORE UPDATE ON construction_cashbook_entries
            BEGIN
                SELECT RAISE(ABORT, 'construction_cashbook_entries are append-only');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_construction_cashbook_no_delete
            BEFORE DELETE ON construction_cashbook_entries
            BEGIN
                SELECT RAISE(ABORT, 'construction_cashbook_entries are append-only');
            END
            """
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_construction_cashbook_no_update ON construction_cashbook_entries")
        op.execute("DROP TRIGGER IF EXISTS trg_construction_cashbook_no_delete ON construction_cashbook_entries")
        op.execute("DROP FUNCTION IF EXISTS reject_construction_cashbook_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_construction_cashbook_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_construction_cashbook_no_delete")
    op.drop_index("ix_construction_cashbook_org_project_created", table_name="construction_cashbook_entries")
    op.drop_table("construction_cashbook_entries")
    op.drop_index("uq_journal_entries_id_org", table_name="journal_entries")
