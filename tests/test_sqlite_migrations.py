"""SQLite migration regression coverage for the project cashbook."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


def test_sqlite_upgrade_installs_cashbook_tenant_keys_and_append_only_triggers(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'hijaz-one-migrations.db'}"
    monkeypatch.setattr(settings, "database_url", database_url)
    repository_root = Path(__file__).resolve().parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0042_workforce_directory"
    table_names = set(inspect(engine).get_table_names())
    assert {
        "user_phone_verifications", "property_listings", "property_transactions", "property_payments",
        "education_settings", "education_student_profiles", "education_guardian_consents",
        "education_teacher_profiles", "education_courses", "education_course_enrollments",
        "education_course_materials", "workforce_profiles",
    } <= table_names
    course_columns = {column["name"] for column in inspect(engine).get_columns("education_courses")}
    assert {"country_code", "city", "price_minor", "commission_bps"} <= course_columns
    workforce_columns = {column["name"] for column in inspect(engine).get_columns("workforce_profiles")}
    assert {"role_key", "skills", "country_code", "moderation_status", "available_cities"} <= workforce_columns
    workforce_constraints = inspect(engine).get_unique_constraints("workforce_profiles")
    assert any(item["name"] == "uq_workforce_user_role" for item in workforce_constraints)
    workforce_indexes = inspect(engine).get_indexes("workforce_profiles")
    assert any(item["name"] == "ix_workforce_public_discovery" for item in workforce_indexes)

    guardian_constraints = inspect(engine).get_unique_constraints("education_guardian_consents")
    assert any(item["name"] == "uq_education_guardian_student" for item in guardian_constraints)
    payment_indexes = inspect(engine).get_indexes("property_payments")
    assert any(item["name"] == "uq_property_listing_fee_payment" and item["unique"] for item in payment_indexes)
    assert any(item["name"] == "uq_property_transaction_payment" and item["unique"] for item in payment_indexes)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    from app.models import (
        Account, ConstructionCashbookEntry, ConstructionClient, ConstructionProject,
        JournalEntry, JournalLine, Organization, User,
    )

    suffix = uuid4().hex
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with factory() as db:
            user = User(email=f"sqlite-cashbook-{suffix}@example.com", password_hash="test-hash")
            org_a = Organization(name=f"SQLite Cashbook A {suffix[:8]}")
            org_b = Organization(name=f"SQLite Cashbook B {suffix[:8]}")
            db.add_all([user, org_a, org_b])
            db.flush()
            client_a = ConstructionClient(
                organization_id=org_a.id, created_by_user_id=user.id, display_name="SQLite Cashbook A"
            )
            client_b = ConstructionClient(
                organization_id=org_b.id, created_by_user_id=user.id, display_name="SQLite Cashbook B"
            )
            db.add_all([client_a, client_b])
            db.flush()
            project_a = ConstructionProject(
                organization_id=org_a.id, client_id=client_a.id, created_by_user_id=user.id,
                title="SQLite project A", work_type="grey_structure", contract_type="labour_material",
                contract_amount_minor=100000, currency="PKR", status="approved",
            )
            project_b = ConstructionProject(
                organization_id=org_b.id, client_id=client_b.id, created_by_user_id=user.id,
                title="SQLite project B", work_type="grey_structure", contract_type="labour_material",
                contract_amount_minor=100000, currency="PKR", status="approved",
            )
            db.add_all([project_a, project_b])
            cash = Account(
                organization_id=org_a.id, code="1000", name="Cash",
                account_type="asset", currency="PKR", is_active=True,
            )
            revenue = Account(
                organization_id=org_a.id, code="4000", name="Revenue",
                account_type="revenue", currency="PKR", is_active=True,
            )
            db.add_all([cash, revenue])
            db.flush()
            journal = JournalEntry(
                organization_id=org_a.id, created_by_user_id=user.id,
                idempotency_key=f"sqlite-cashbook-{suffix}", request_fingerprint="b" * 64,
                description="SQLite cashbook migration test", currency="PKR", total_minor=1000, status="posted",
            )
            db.add(journal)
            db.flush()
            db.add_all([
                JournalLine(journal_entry_id=journal.id, account_id=cash.id, debit_minor=1000, credit_minor=0),
                JournalLine(journal_entry_id=journal.id, account_id=revenue.id, debit_minor=0, credit_minor=1000),
            ])
            cashbook = ConstructionCashbookEntry(
                organization_id=org_a.id, project_id=project_a.id, journal_entry_id=journal.id,
                created_by_user_id=user.id, direction="income", category="Advance",
                amount_minor=1000, currency="PKR",
            )
            db.add(cashbook)
            db.commit()
            cashbook_id = cashbook.id
            project_b_id, journal_id, user_id, org_b_id = project_b.id, journal.id, user.id, org_b.id

        with factory() as db:
            db.add(ConstructionCashbookEntry(
                organization_id=org_b_id, project_id=project_b_id, journal_entry_id=journal_id,
                created_by_user_id=user_id, direction="income", category="Cross-tenant journal",
                amount_minor=1000, currency="PKR",
            ))
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()

        with pytest.raises(IntegrityError, match="construction_cashbook_entries are append-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE construction_cashbook_entries SET category = 'tampered' WHERE id = :id"),
                    {"id": cashbook_id},
                )
        with pytest.raises(IntegrityError, match="construction_cashbook_entries are append-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM construction_cashbook_entries WHERE id = :id"),
                    {"id": cashbook_id},
                )
    finally:
        engine.dispose()
