"""Enforce open accounting periods for journal-entry inserts."""
from typing import Sequence, Union

from alembic import op

revision: str = "0027_journal_period_guard"
down_revision: Union[str, None] = "0026_bank_reconciliation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION require_open_accounting_period_for_journal() RETURNS trigger AS $function$
            DECLARE
                entry_date date;
            BEGIN
                PERFORM id FROM organizations WHERE id = NEW.organization_id FOR UPDATE;
                entry_date := (NEW.created_at AT TIME ZONE 'UTC')::date;
                IF EXISTS (
                    SELECT 1 FROM accounting_periods
                    WHERE organization_id = NEW.organization_id
                ) THEN
                    PERFORM id FROM accounting_periods
                    WHERE organization_id = NEW.organization_id
                      AND starts_on <= entry_date
                      AND ends_on >= entry_date
                    FOR UPDATE;
                    IF NOT EXISTS (
                        SELECT 1 FROM accounting_periods
                        WHERE organization_id = NEW.organization_id
                          AND status = 'open'
                          AND starts_on <= entry_date
                          AND ends_on >= entry_date
                    ) THEN
                        RAISE EXCEPTION 'no open accounting period covers journal entry date';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $function$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_journal_entries_open_period
            BEFORE INSERT ON journal_entries
            FOR EACH ROW EXECUTE FUNCTION require_open_accounting_period_for_journal()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_journal_entries_open_period
            BEFORE INSERT ON journal_entries
            WHEN EXISTS (
                SELECT 1 FROM accounting_periods
                WHERE organization_id = NEW.organization_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM accounting_periods
                WHERE organization_id = NEW.organization_id
                  AND status = 'open'
                  AND starts_on <= date(NEW.created_at)
                  AND ends_on >= date(NEW.created_at)
            )
            BEGIN
                SELECT RAISE(ABORT, 'no open accounting period covers journal entry date');
            END
            """
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_journal_entries_open_period ON journal_entries")
        op.execute("DROP FUNCTION IF EXISTS require_open_accounting_period_for_journal()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_journal_entries_open_period")
