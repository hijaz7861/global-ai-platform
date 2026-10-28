"""Enforce non-overlapping accounting periods in the database."""
from typing import Sequence, Union

from alembic import op

revision: str = "0025_accounting_period_overlap"
down_revision: Union[str, None] = "0024_audit_append_only"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_overlapping_accounting_period() RETURNS trigger AS $$
            BEGIN
                PERFORM id FROM organizations WHERE id = NEW.organization_id FOR UPDATE;
                IF EXISTS (
                    SELECT 1 FROM accounting_periods
                    WHERE organization_id = NEW.organization_id
                      AND id <> NEW.id
                      AND starts_on <= NEW.ends_on
                      AND ends_on >= NEW.starts_on
                ) THEN
                    RAISE EXCEPTION 'accounting periods must not overlap';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_accounting_periods_no_overlap
            BEFORE INSERT OR UPDATE ON accounting_periods
            FOR EACH ROW EXECUTE FUNCTION reject_overlapping_accounting_period()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_accounting_periods_no_overlap_insert
            BEFORE INSERT ON accounting_periods
            WHEN EXISTS (
                SELECT 1 FROM accounting_periods
                WHERE organization_id = NEW.organization_id
                  AND starts_on <= NEW.ends_on
                  AND ends_on >= NEW.starts_on
            )
            BEGIN
                SELECT RAISE(ABORT, 'accounting periods must not overlap');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_accounting_periods_no_overlap_update
            BEFORE UPDATE ON accounting_periods
            WHEN EXISTS (
                SELECT 1 FROM accounting_periods
                WHERE organization_id = NEW.organization_id
                  AND id <> NEW.id
                  AND starts_on <= NEW.ends_on
                  AND ends_on >= NEW.starts_on
            )
            BEGIN
                SELECT RAISE(ABORT, 'accounting periods must not overlap');
            END
            """
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_accounting_periods_no_overlap ON accounting_periods")
        op.execute("DROP FUNCTION IF EXISTS reject_overlapping_accounting_period()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_accounting_periods_no_overlap_insert")
        op.execute("DROP TRIGGER IF EXISTS trg_accounting_periods_no_overlap_update")