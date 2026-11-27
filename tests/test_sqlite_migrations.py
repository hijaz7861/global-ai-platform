"""SQLite migration regression coverage for the project cashbook."""

from pathlib import Path
from decimal import Decimal
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
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0050_ai_agent_checkpoints"
    table_names = set(inspect(engine).get_table_names())
    assert {
        "user_phone_verifications", "property_listings", "property_transactions", "property_payments",
        "education_settings", "education_student_profiles", "education_guardian_consents",
        "education_teacher_profiles", "education_courses", "education_course_enrollments",
        "education_course_materials", "workforce_profiles", "workforce_service_requests", "workforce_service_payments",
        "workforce_service_messages", "workforce_message_reports", "workforce_message_appeals",
        "customer_engagement_leads", "customer_engagement_consent_events",
        "customer_engagement_unsubscribe_tokens",
        "construction_labour_entries", "construction_material_entries",
    } <= table_names
    course_columns = {column["name"] for column in inspect(engine).get_columns("education_courses")}
    assert {"country_code", "city", "price_minor", "commission_bps"} <= course_columns
    service_request_columns = {column["name"] for column in inspect(engine).get_columns("workforce_service_requests")}
    assert {"client_user_id", "provider_profile_id", "client_phone_verified_at", "platform_fee_status", "client_terms_version"} <= service_request_columns
    message_columns = {column["name"] for column in inspect(engine).get_columns("workforce_service_messages")}
    assert {"service_request_id", "sender_user_id", "sender_side", "moderation_status", "body", "created_at"} <= message_columns
    message_indexes = inspect(engine).get_indexes("workforce_service_messages")
    assert any(item["name"] == "ix_workforce_service_message_request_created" for item in message_indexes)
    message_checks = inspect(engine).get_check_constraints("workforce_service_messages")
    assert any("16384" in item["sqltext"] for item in message_checks)

    labour_columns = {column["name"] for column in inspect(engine).get_columns("construction_labour_entries")}
    assert {
        "organization_id", "project_id", "created_by_user_id", "worker_name", "work_date",
        "worker_count", "days", "daily_rate_minor", "total_amount_minor", "currency",
    } <= labour_columns
    material_columns = {column["name"] for column in inspect(engine).get_columns("construction_material_entries")}
    assert {
        "organization_id", "project_id", "created_by_user_id", "item_name", "quantity", "unit",
        "unit_price_minor", "total_amount_minor", "supplier", "entry_date", "currency",
    } <= material_columns
    labour_foreign_keys = inspect(engine).get_foreign_keys("construction_labour_entries")
    material_foreign_keys = inspect(engine).get_foreign_keys("construction_material_entries")
    assert any(item["constrained_columns"] == ["project_id", "organization_id"] for item in labour_foreign_keys)
    assert any(item["constrained_columns"] == ["project_id", "organization_id"] for item in material_foreign_keys)
    with engine.connect() as connection:
        trigger_names = {
            row[0] for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
    assert {
        "trg_construction_labour_no_update", "trg_construction_labour_no_delete",
        "trg_construction_material_no_update", "trg_construction_material_no_delete",
    } <= trigger_names

    service_payment_indexes = inspect(engine).get_indexes("workforce_service_payments")
    assert any(item["name"] == "uq_workforce_service_payment_active" and item["unique"] for item in service_payment_indexes)

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

    from app.models import (
        Account, ConstructionCashbookEntry, ConstructionClient, ConstructionLabourEntry,
        ConstructionMaterialEntry, ConstructionProject, JournalEntry, JournalLine, Organization, User, utc_now,
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
            labour = ConstructionLabourEntry(
                organization_id=org_a.id, project_id=project_a.id, created_by_user_id=user.id,
                worker_name="Migration test crew", work_date=utc_now().date(), worker_count=2,
                days=Decimal("1.50"), daily_rate_minor=12000, total_amount_minor=36000, currency="PKR",
            )
            material = ConstructionMaterialEntry(
                organization_id=org_a.id, project_id=project_a.id, created_by_user_id=user.id,
                item_name="Cement", quantity=Decimal("1.250"), unit="bag",
                unit_price_minor=500, total_amount_minor=625, supplier="Local supplier",
                entry_date=utc_now().date(), currency="PKR",
            )
            db.add_all([labour, material])
            db.flush()
            labour_id, material_id = labour.id, material.id
            project_a_id = project_a.id
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

        with factory() as db:
            db.add(ConstructionLabourEntry(
                organization_id=org_b_id, project_id=project_a_id, created_by_user_id=user_id,
                worker_name="Cross tenant", work_date=utc_now().date(), worker_count=1,
                days=Decimal("1.00"), daily_rate_minor=10, total_amount_minor=10, currency="PKR",
            ))
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()
            db.add(ConstructionMaterialEntry(
                organization_id=org_b_id, project_id=project_a_id, created_by_user_id=user_id,
                item_name="Cross tenant", quantity=Decimal("1.000"), unit="bag",
                unit_price_minor=10, total_amount_minor=10, entry_date=utc_now().date(), currency="PKR",
            ))
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()

        with pytest.raises(IntegrityError, match="construction_labour_entries are append-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE construction_labour_entries SET worker_name = 'tampered' WHERE id = :id"),
                    {"id": labour_id},
                )
        with pytest.raises(IntegrityError, match="construction_labour_entries are append-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM construction_labour_entries WHERE id = :id"),
                    {"id": labour_id},
                )
        with pytest.raises(IntegrityError, match="construction_material_entries are append-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE construction_material_entries SET item_name = 'tampered' WHERE id = :id"),
                    {"id": material_id},
                )
        with pytest.raises(IntegrityError, match="construction_material_entries are append-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM construction_material_entries WHERE id = :id"),
                    {"id": material_id},
                )

    finally:
        engine.dispose()



def test_workforce_message_encryption_migration_encrypts_existing_rows(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("WORKFORCE_MESSAGE_ENCRYPTION_KEY", key)
    database_url = f"sqlite:///{tmp_path / 'workforce-message-encryption.db'}"
    monkeypatch.setattr(settings, "database_url", database_url)
    repository_root = Path(__file__).resolve().parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0044_workforce_contact_relay")

    engine = create_engine(database_url)
    plaintext = "Legacy workforce message that must not remain in the database"
    timestamp = "2026-11-23 18:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_active, created_at) "
                "VALUES (:id, :email, :password_hash, 1, :created_at)"
            ),
            {
                "id": "encryption-user",
                "email": "encryption-migration@example.com",
                "password_hash": "test-hash",
                "created_at": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO workforce_service_requests "
                "(id, request_code, client_user_id, service_type, title, details, city, "
                "country_code, currency, client_phone_verified_at, client_terms_version, "
                "client_terms_accepted_at, status, platform_fee_minor, "
                "platform_fee_currency, platform_fee_status, created_at, updated_at) "
                "VALUES (:id, :code, :user_id, 'hire_worker', 'Legacy request', "
                "'A sufficiently detailed legacy request', 'Lahore', 'PK', 'PKR', "
                ":verified_at, 'terms-v1', :accepted_at, 'matched', 5000, 'PKR', "
                "'verified', :created_at, :updated_at)"
            ),
            {
                "id": "encryption-request",
                "code": "HJZ-ENCRYPT-0001",
                "user_id": "encryption-user",
                "verified_at": timestamp,
                "accepted_at": timestamp,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO workforce_service_messages "
                "(id, service_request_id, sender_user_id, sender_side, body, created_at) "
                "VALUES ('encryption-message', 'encryption-request', 'encryption-user', "
                "'client', :body, :created_at)"
            ),
            {"body": plaintext, "created_at": timestamp},
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        stored = connection.scalar(
            text(
                "SELECT body FROM workforce_service_messages "
                "WHERE id = 'encryption-message'"
            )
        )
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
    engine.dispose()

    assert version == "0050_ai_agent_checkpoints"
    assert stored.startswith("fernet:v1:")
    with engine.connect() as connection:
        moderation_status = connection.scalar(
            text(
                "SELECT moderation_status FROM workforce_service_messages "
                "WHERE id = 'encryption-message'"
            )
        )
        report_columns = {
            column["name"]
            for column in inspect(engine).get_columns("workforce_message_reports")
        }
    assert moderation_status == "visible"
    assert {"message_id", "reporter_user_id", "reason_code", "status"} <= report_columns
    assert "body" not in report_columns
    appeal_columns = {
        column["name"]
        for column in inspect(engine).get_columns("workforce_message_appeals")
    }
    assert {
        "message_id", "appellant_user_id", "reason_code", "status",
        "reviewed_by_user_id", "reviewed_at", "created_at",
    } <= appeal_columns
    assert "body" not in appeal_columns
    appeal_constraints = inspect(engine).get_unique_constraints("workforce_message_appeals")
    assert any(
        item["name"] == "uq_workforce_message_appeal_message_appellant"
        for item in appeal_constraints
    )
    assert plaintext not in stored
    token = stored[len("fernet:v1:"):].encode("ascii")
    assert Fernet(key.encode("ascii")).decrypt(token).decode("utf-8") == plaintext


def test_sqlite_customer_engagement_consent_events_are_append_only(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'engagement-consent.db'}"
    monkeypatch.setattr(settings, "database_url", database_url)
    repository_root = Path(__file__).resolve().parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    user_id, lead_id = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, password_hash, created_at) VALUES (:id, :email, 'test-hash', CURRENT_TIMESTAMP)"),
            {"id": user_id, "email": f"consent-{user_id}@example.com"},
        )
        connection.execute(
            text("INSERT INTO customer_engagement_leads (id, user_id, interest_text) VALUES (:id, :user_id, 'Construction quote')"),
            {"id": lead_id, "user_id": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO customer_engagement_consent_events "
                "(lead_id, user_id, channel, consent, policy_version, source) "
                "VALUES (:lead_id, :user_id, 'marketing', 1, 'test-v1', 'test')"
            ),
            {"lead_id": lead_id, "user_id": user_id},
        )

    with pytest.raises(IntegrityError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE customer_engagement_consent_events SET consent = 0 WHERE lead_id = :lead_id"),
                {"lead_id": lead_id},
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM customer_engagement_consent_events WHERE lead_id = :lead_id"),
                {"lead_id": lead_id},
            )

    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT consent FROM customer_engagement_consent_events WHERE lead_id = :lead_id"),
            {"lead_id": lead_id},
        ) == 1
    engine.dispose()
