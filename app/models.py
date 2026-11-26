"""Identity, commerce, audit, accounting, and security data models."""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=true(), nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(String(256), nullable=True)
    mfa_pending_secret_encrypted: Mapped[str | None] = mapped_column(String(256), nullable=True)
    mfa_pending_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_last_verified_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    listings: Mapped[list["Listing"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class MembershipInvitation(Base):
    """Single-use, expiring invitation for an organization membership."""
    __tablename__ = "membership_invitations"
    __table_args__ = (
        Index("ix_membership_invites_org_email", "organization_id", "email"),
        Index("ix_membership_invites_expiry", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32), default="member", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint("price_minor > 0", name="ck_listings_price_positive"),
        CheckConstraint("length(currency) = 3", name="ck_listings_currency_length"),
        CheckConstraint("inventory_quantity >= 0", name="ck_listings_inventory_nonnegative"),
        Index("ix_listings_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(5000), nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    inventory_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="listings")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_orders_user_idempotency"),
        CheckConstraint("total_minor > 0", name="ck_orders_total_positive"),
        Index("ix_orders_user_created", "user_id", "created_at"),
        Index("ix_orders_status_expires", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending_payment", nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        CheckConstraint("unit_price_minor > 0", name="ck_order_items_price_positive"),
        Index("ix_order_items_order_id", "order_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="RESTRICT"), nullable=False)
    listing_organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_org_created", "organization_id", "created_at"),
        Index("ix_audit_events_actor_created", "actor_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", "currency", name="uq_accounts_org_code_currency"),
        CheckConstraint("length(currency) = 3", name="ck_accounts_currency_length"),
        CheckConstraint(
            "account_type IN ('asset','liability','equity','revenue','expense')",
            name="ck_accounts_type",
        ),
        Index("ix_accounts_org_active", "organization_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    lines: Mapped[list["JournalLine"]] = relationship(back_populates="account")


class InventoryAdjustmentHistory(Base):
    """Append-only record of each explicit stock correction."""
    __tablename__ = "inventory_adjustments"
    __table_args__ = (
        CheckConstraint("delta != 0", name="ck_inventory_adjustments_nonzero_delta"),
        CheckConstraint("quantity_before >= 0 AND quantity_after >= 0", name="ck_inventory_adjustments_nonnegative_quantities"),
        Index("ix_inventory_adjustments_listing_created", "listing_id", "created_at"),
        Index("ix_inventory_adjustments_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="RESTRICT"), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_before: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AccountingPeriod(Base):
    """Organization-scoped accounting window; closed periods cannot be reopened."""
    __tablename__ = "accounting_periods"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_accounting_period_org_name"),
        CheckConstraint("ends_on >= starts_on", name="ck_accounting_period_date_order"),
        CheckConstraint("status IN ('open','closed')", name="ck_accounting_period_status"),
        Index("ix_accounting_period_org_dates", "organization_id", "starts_on", "ends_on"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    closed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_journal_org_idempotency"),
        Index("uq_journal_entries_id_org", "id", "organization_id", unique=True),
        CheckConstraint("total_minor > 0", name="ck_journal_total_positive"),
        CheckConstraint("status = 'posted'", name="ck_journal_posted_only"),
        UniqueConstraint("reversal_of_id", name="uq_journal_reversal_source"),
        Index("ix_journal_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reversal_of_id: Mapped[str | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="posted", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="JournalLine.id"
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("debit_minor >= 0 AND credit_minor >= 0", name="ck_journal_lines_nonnegative"),
        CheckConstraint(
            "(debit_minor > 0 AND credit_minor = 0) OR (credit_minor > 0 AND debit_minor = 0)",
            name="ck_journal_lines_one_side",
        ),
        Index("ix_journal_lines_entry", "journal_entry_id"),
        Index("ix_journal_lines_account", "account_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    journal_entry_id: Mapped[str] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    debit_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    credit_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship(back_populates="lines")


class BankReconciliationSession(Base):
    """A manually imported bank statement window for one tenant-owned asset account."""
    __tablename__ = "bank_reconciliation_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('open','completed')", name="ck_bank_reconciliation_status"),
        CheckConstraint("statement_end_date >= statement_start_date", name="ck_bank_reconciliation_dates"),
        UniqueConstraint(
            "organization_id", "account_id", "statement_start_date", "statement_end_date",
            name="uq_bank_reconciliation_account_window",
        ),
        Index("ix_bank_reconciliation_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    statement_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    statement_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    closing_balance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    lines: Mapped[list["BankReconciliationLine"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="BankReconciliationLine.statement_date"
    )


class BankReconciliationImportBatch(Base):
    """Auditable provenance record for one bounded statement CSV import."""
    __tablename__ = "bank_reconciliation_import_batches"
    __table_args__ = (
        UniqueConstraint("session_id", "content_sha256", name="uq_recon_import_session_digest"),
        Index("ix_recon_import_session_created", "session_id", "created_at"),
        CheckConstraint("row_count >= 0", name="ck_recon_import_row_count"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("bank_reconciliation_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class BankReconciliationLine(Base):
    """A signed bank statement movement matched to at most one posted cash-account journal line."""
    __tablename__ = "bank_reconciliation_lines"
    __table_args__ = (
        CheckConstraint("amount_minor != 0", name="ck_bank_reconciliation_line_nonzero_amount"),
        UniqueConstraint("matched_journal_line_id", name="uq_bank_reconciliation_matched_journal_line"),
        UniqueConstraint("session_id", "external_reference", name="uq_bank_reconciliation_external_reference"),
        Index("ix_bank_reconciliation_lines_session_date", "session_id", "statement_date"),
        Index("ix_recon_lines_import_batch", "import_batch_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("bank_reconciliation_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    statement_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    import_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("bank_reconciliation_import_batches.id", ondelete="RESTRICT"), nullable=True
    )
    matched_journal_line_id: Mapped[str | None] = mapped_column(
        ForeignKey("journal_lines.id", ondelete="RESTRICT"), nullable=True
    )
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    session: Mapped[BankReconciliationSession] = relationship(back_populates="lines")


class TokenRevocation(Base):
    __tablename__ = "token_revocations"
    __table_args__ = (Index("ix_token_revocations_expires_at", "expires_at"),)

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class RefreshToken(Base):
    """Hashed, rotating refresh credentials grouped into revocable session families."""
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_family", "family_id"),
        Index("ix_refresh_tokens_user_expires", "user_id", "expires_at"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class EmailVerificationToken(Base):
    """Single-use email ownership token; only its digest is persisted."""
    __tablename__ = "email_verification_tokens"
    __table_args__ = (
        Index("ix_email_verification_user_expiry", "user_id", "expires_at"),
        Index("ix_email_verification_expiry", "expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class PasswordResetToken(Base):
    """One-time reset token; only a SHA-256 digest is stored."""
    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_user_expiry", "user_id", "expires_at"), Index("ix_password_reset_expiry", "expires_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class SupplierProfile(Base):
    """Tenant-owned supplier onboarding profile with an explicit review state."""
    __tablename__ = "supplier_profiles"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_supplier_profiles_organization"),
        CheckConstraint("status IN ('draft','submitted','approved','rejected')", name="ck_supplier_profiles_status"),
        CheckConstraint("length(country_code) = 2", name="ck_supplier_profiles_country_code"),
        Index("ix_supplier_profiles_status_created", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_type: Mapped[str] = mapped_column(String(32), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False)
    website_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    description: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    reviewer_notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)



class TransportProviderProfile(Base):
    """Organization-owned transport provider profile with explicit moderation state."""
    __tablename__ = "transport_provider_profiles"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_transport_provider_profiles_org"),
        UniqueConstraint("id", "organization_id", name="uq_transport_provider_profiles_id_org"),
        CheckConstraint("status IN ('draft','submitted','approved','rejected','suspended')",
                        name="ck_transport_provider_profiles_status"),
        CheckConstraint("length(country_code) = 2 AND country_code = upper(country_code)",
                        name="ck_transport_provider_profiles_country"),
        Index("ix_transport_provider_profiles_status_city", "status", "country_code", "city"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state_region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="PK", nullable=False)
    service_description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    review_notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class TransportVehicle(Base):
    """Tenant-scoped vehicle or machinery listing; private evidence is not stored here."""
    __tablename__ = "transport_vehicles"
    __table_args__ = (
        CheckConstraint(
            "vehicle_type IN ('loader_rickshaw','qingqi','tractor_trolley','tractor_bucket',"
            "'dumper','truck','mazda','pickup','crane','jcb','excavator','backhoe_loader',"
            "'forklift','water_tanker','concrete_mixer','other')",
            name="ck_transport_vehicles_type"),
        CheckConstraint("pricing_basis IN ('per_trip','per_hour','per_day','per_km','fixed_job')",
                        name="ck_transport_vehicles_pricing_basis"),
        CheckConstraint("rate_minor >= 0", name="ck_transport_vehicles_rate_nonnegative"),
        CheckConstraint("minimum_charge_minor IS NULL OR minimum_charge_minor >= 0",
                        name="ck_transport_vehicles_minimum_nonnegative"),
        CheckConstraint("length(currency) = 3 AND currency = upper(currency)",
                        name="ck_transport_vehicles_currency"),
        CheckConstraint("length(country_code) = 2 AND country_code = upper(country_code)",
                        name="ck_transport_vehicles_country"),
        CheckConstraint("service_radius_km BETWEEN 1 AND 2000",
                        name="ck_transport_vehicles_service_radius"),
        CheckConstraint("availability IN ('available','busy','offline')",
                        name="ck_transport_vehicles_availability"),
        CheckConstraint("verification_status IN ('pending','verified','rejected','suspended')",
                        name="ck_transport_vehicles_verification"),
        Index("ix_transport_vehicles_discovery", "verification_status", "availability",
              "vehicle_type", "country_code", "city"),
        Index("ix_transport_vehicles_org_created", "organization_id", "created_at"),
        Index("ix_transport_vehicles_profile", "provider_profile_id"),
        ForeignKeyConstraint(
            ["provider_profile_id", "organization_id"],
            ["transport_provider_profiles.id", "transport_provider_profiles.organization_id"],
            ondelete="RESTRICT", name="fk_transport_vehicles_profile_org",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    provider_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    make_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    capacity_description: Mapped[str | None] = mapped_column(String(160), nullable=True)
    operator_included: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state_region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="PK", nullable=False)
    pricing_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    rate_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    minimum_charge_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    service_radius_km: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    description: Mapped[str] = mapped_column(String(1500), default="", nullable=False)
    availability: Mapped[str] = mapped_column(String(16), default="available", nullable=False)
    verification_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    review_notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class ProcurementRequestRecord(Base):
    """A buyer organization's bounded request for supplier quotations."""
    __tablename__ = "procurement_requests"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_procurement_requests_quantity_positive"),
        CheckConstraint("length(currency) = 3", name="ck_procurement_requests_currency_length"),
        CheckConstraint("status IN ('open','awarded','closed')", name="ck_procurement_requests_status"),
        Index("ix_procurement_requests_org_status_created", "buyer_organization_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    buyer_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(3000), default="", nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    awarded_quote_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "supplier_quotes.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_procurement_requests_awarded_quote_id_supplier_quotes",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class SupplierQuote(Base):
    """A supplier's one quote per procurement request."""
    __tablename__ = "supplier_quotes"
    __table_args__ = (
        UniqueConstraint("procurement_request_id", "supplier_organization_id", name="uq_supplier_quote_request_supplier"),
        CheckConstraint("unit_price_minor > 0", name="ck_supplier_quotes_price_positive"),
        CheckConstraint("lead_time_days >= 0", name="ck_supplier_quotes_lead_time_nonnegative"),
        CheckConstraint("status IN ('submitted','accepted','rejected','withdrawn')", name="ck_supplier_quotes_status"),
        Index("ix_supplier_quotes_request_status", "procurement_request_id", "status"),
        Index("ix_supplier_quotes_supplier_created", "supplier_organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    procurement_request_id: Mapped[str] = mapped_column(ForeignKey("procurement_requests.id", ondelete="RESTRICT"), nullable=False)
    supplier_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="submitted", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class SupplierQuoteRevision(Base):
    """Append-only snapshots of supplier quote submissions, revisions, and withdrawals."""
    __tablename__ = "supplier_quote_revisions"
    __table_args__ = (
        UniqueConstraint("supplier_quote_id", "revision_number", name="uq_supplier_quote_revision_number"),
        CheckConstraint("revision_number > 0", name="ck_supplier_quote_revision_number_positive"),
        CheckConstraint("unit_price_minor > 0", name="ck_supplier_quote_revision_price_positive"),
        CheckConstraint("lead_time_days >= 0", name="ck_supplier_quote_revision_lead_time_nonnegative"),
        CheckConstraint("status IN ('submitted','withdrawn')", name="ck_supplier_quote_revisions_status"),
        Index("ix_supplier_quote_revisions_quote_created", "supplier_quote_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    supplier_quote_id: Mapped[str] = mapped_column(ForeignKey("supplier_quotes.id", ondelete="RESTRICT"), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class PurchaseOrder(Base):
    """Issued purchase commitment for the awarded supplier quote."""
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("procurement_request_id", name="uq_purchase_order_request"),
        UniqueConstraint("supplier_quote_id", name="uq_purchase_order_quote"),
        CheckConstraint("quantity > 0", name="ck_purchase_orders_quantity_positive"),
        CheckConstraint("unit_price_minor > 0", name="ck_purchase_orders_unit_price_positive"),
        CheckConstraint("total_minor > 0", name="ck_purchase_orders_total_positive"),
        CheckConstraint("status IN ('issued','fulfilled','cancelled')", name="ck_purchase_orders_status"),
        Index("ix_purchase_orders_buyer_created", "buyer_organization_id", "created_at"),
        Index("ix_purchase_orders_supplier_status", "supplier_organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    procurement_request_id: Mapped[str] = mapped_column(
        ForeignKey("procurement_requests.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_quote_id: Mapped[str] = mapped_column(
        ForeignKey("supplier_quotes.id", ondelete="RESTRICT"), nullable=False
    )
    buyer_organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="issued", nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class MFARecoveryCode(Base):
    """One-time, hash-only recovery credentials for MFA-protected accounts."""
    __tablename__ = "mfa_recovery_codes"
    __table_args__ = (
        Index("ix_mfa_recovery_codes_user_used", "user_id", "used_at"),
        Index("ix_mfa_recovery_codes_created", "created_at"),
    )

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AuthRateLimitLock(Base):
    """Per-subject serialization row for atomic auth rate-limit accounting."""
    __tablename__ = "auth_rate_limit_locks"
    __table_args__ = (Index("ix_auth_rate_limit_locks_touched", "touched_at"),)

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    touched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AuthAttempt(Base):
    """Privacy-minimized persistent rate-limit events for sensitive auth endpoints."""
    __tablename__ = "auth_attempts"
    __table_args__ = (
        Index("ix_auth_attempt_scope_subject_created", "scope", "subject_hash", "created_at"),
        Index("ix_auth_attempt_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AITask(Base):
    """Durable tenant-scoped unit of AI work with a renewable worker lease."""
    __tablename__ = "ai_tasks"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "created_by_user_id", "idempotency_key",
            name="uq_ai_task_actor_idempotency",
        ),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_ai_tasks_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 10",
            name="ck_ai_tasks_attempts",
        ),
        Index("ix_ai_tasks_claim", "status", "available_at", "created_at"),
        Index("ix_ai_tasks_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AITaskStep(Base):
    """Append-oriented execution attempt record; payload content is represented by digests."""
    __tablename__ = "ai_task_steps"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence_number", name="uq_ai_task_step_sequence"),
        CheckConstraint(
            "status IN ('running','succeeded','failed','abandoned')",
            name="ck_ai_task_steps_status",
        ),
        CheckConstraint("sequence_number > 0", name="ck_ai_task_steps_sequence_positive"),
        Index("ix_ai_task_steps_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("ai_tasks.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AITaskEvent(Base):
    """Minimal task lifecycle event; deliberately excludes prompts and raw outputs."""
    __tablename__ = "ai_task_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('created','claimed','heartbeat','completed','retry_scheduled','failed','cancel_requested','cancelled','lease_expired')",
            name="ck_ai_task_events_type",
        ),
        Index("ix_ai_task_events_org_created", "organization_id", "created_at"),
        Index("ix_ai_task_events_task_created", "task_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("ai_tasks.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AIOutboxEvent(Base):
    """Transactional delivery record for a committed AI task lifecycle event."""
    __tablename__ = "ai_outbox_events"
    __table_args__ = (
        UniqueConstraint("task_event_id", name="uq_ai_outbox_task_event"),
        CheckConstraint(
            "status IN ('pending','processing','delivered','dead')",
            name="ck_ai_outbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 20",
            name="ck_ai_outbox_attempts",
        ),
        Index("ix_ai_outbox_dispatch", "status", "available_at", "created_at"),
        Index("ix_ai_outbox_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_event_id: Mapped[str] = mapped_column(
        ForeignKey("ai_task_events.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("ai_tasks.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AIMemoryRecord(Base):
    """Provenance-aware user-private or organization-shared AI memory."""
    __tablename__ = "ai_memory_records"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'user' AND owner_user_id IS NOT NULL AND organization_id IS NULL) OR "
            "(scope = 'organization' AND organization_id IS NOT NULL AND owner_user_id IS NULL)",
            name="ck_ai_memory_scope_owner",
        ),
        CheckConstraint("scope IN ('user','organization')", name="ck_ai_memory_scope"),
        CheckConstraint(
            "memory_type IN ('preference','feedback','project','reference','decision','session_summary')",
            name="ck_ai_memory_type",
        ),
        CheckConstraint("sensitivity IN ('normal','sensitive','restricted')", name="ck_ai_memory_sensitivity"),
        CheckConstraint(
            "verification_status IN ('unverified','verified','superseded')",
            name="ck_ai_memory_verification",
        ),
        CheckConstraint("status IN ('active','archived','superseded')", name="ck_ai_memory_status"),
        CheckConstraint("confidence_percent BETWEEN 0 AND 100", name="ck_ai_memory_confidence"),
        Index("ix_ai_memory_org_scope_status", "organization_id", "scope", "status", "updated_at"),
        Index("ix_ai_memory_owner_scope_status", "owner_user_id", "scope", "status", "updated_at"),
        Index("ix_ai_memory_type_created", "memory_type", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    memory_type: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    confidence_percent: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    verification_status: Mapped[str] = mapped_column(String(16), default="unverified", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_memory_records.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class AISkillVersion(Base):
    """Version-pinned instructional skill content; never executable authorization."""
    __tablename__ = "ai_skill_versions"
    __table_args__ = (
        UniqueConstraint("organization_id", "skill_id", "version", name="uq_ai_skill_org_id_version"),
        CheckConstraint(
            "status IN ('draft','approved','disabled','deprecated')",
            name="ck_ai_skill_status",
        ),
        CheckConstraint(
            "risk_level IN ('low','medium','high','critical')",
            name="ck_ai_skill_risk",
        ),
        CheckConstraint(
            "source_type IN ('internal','user_authored','imported')",
            name="ck_ai_skill_source_type",
        ),
        CheckConstraint("length(content_sha256) = 64", name="ck_ai_skill_digest_length"),
        Index("ix_ai_skill_org_status", "organization_id", "status", "skill_id"),
        Index("ix_ai_skill_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    license_id: Mapped[str] = mapped_column(String(64), nullable=False)
    required_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    data_classes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)



class ConsumedApprovalNonce(Base):
    """Single-use approval nonce shared by all PostgreSQL application workers."""
    __tablename__ = "consumed_approval_nonces"
    __table_args__ = (
        Index("ix_consumed_approval_nonces_time", "consumed_at"),
    )

    nonce: Mapped[str] = mapped_column(String(128), primary_key=True)
    consumed_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RoleDefinition(Base):
    """Platform-governed role catalogue with independently configurable fees."""
    __tablename__ = "role_definitions"
    __table_args__ = (
        CheckConstraint("min_age >= 0 AND min_age <= 120", name="ck_role_definitions_min_age"),
        CheckConstraint(
            "fee_amount_minor IS NULL OR fee_amount_minor >= 0",
            name="ck_role_definitions_fee_nonnegative",
        ),
        CheckConstraint("length(fee_currency) = 3", name="ck_role_definitions_currency"),
    )

    role_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    min_age: Mapped[int] = mapped_column(Integer, default=18, nullable=False)
    fee_amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fee_currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class UserRoleEntitlement(Base):
    """A user's independent request, payment and approval lifecycle for one platform role."""
    __tablename__ = "user_role_entitlements"
    __table_args__ = (
        Index(
            "uq_user_role_entitlement_global", "user_id", "role_key", unique=True,
            postgresql_where=text("organization_id IS NULL AND status IN ('pending_payment', 'pending_review', 'active', 'suspended')"),
            sqlite_where=text("organization_id IS NULL AND status IN ('pending_payment', 'pending_review', 'active', 'suspended')"),
        ),
        Index(
            "uq_user_role_entitlement_org", "user_id", "role_key", "organization_id", unique=True,
            postgresql_where=text("organization_id IS NOT NULL AND status IN ('pending_payment', 'pending_review', 'active', 'suspended')"),
            sqlite_where=text("organization_id IS NOT NULL AND status IN ('pending_payment', 'pending_review', 'active', 'suspended')"),
        ),
        CheckConstraint(
            "status IN ('pending_payment','pending_review','active','rejected','expired','suspended','revoked')",
            name="ck_user_role_entitlements_status",
        ),
        CheckConstraint(
            "payment_status IN ('not_required','pending','verified','rejected')",
            name="ck_user_role_entitlements_payment_status",
        ),
        CheckConstraint("fee_amount_minor >= 0", name="ck_user_role_entitlements_fee_nonnegative"),
        Index("ix_user_role_entitlements_user_status", "user_id", "status"),
        Index("ix_user_role_entitlements_role_status", "role_key", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_key: Mapped[str] = mapped_column(ForeignKey("role_definitions.role_key", ondelete="RESTRICT"), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending_review", nullable=False)
    payment_status: Mapped[str] = mapped_column(String(24), default="not_required", nullable=False)
    fee_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    fee_currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    payment_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    age_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reviewer_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class RoleEntitlementEvent(Base):
    """Append-only audit trail for role application, payment and review transitions."""
    __tablename__ = "role_entitlement_events"
    __table_args__ = (
        Index("ix_role_entitlement_events_entitlement_created", "entitlement_id", "created_at"),
        Index("ix_role_entitlement_events_actor_created", "actor_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    entitlement_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ConstructionClient(Base):
    """Tenant-owned construction client; contact PII is excluded from this first slice."""
    __tablename__ = "construction_clients"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_construction_clients_id_org"),
        Index("ix_construction_clients_org_name", "organization_id", "display_name"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ConstructionProject(Base):
    """Organization-scoped construction project with bounded lifecycle and PKR minor-unit contract value."""
    __tablename__ = "construction_projects"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_construction_projects_id_org"),
        ForeignKeyConstraint(["client_id", "organization_id"],
            ["construction_clients.id", "construction_clients.organization_id"],
            ondelete="RESTRICT", name="fk_construction_projects_client_org"),
        CheckConstraint("work_type IN ('grey_structure','superstructure','complete_construction','renovation','other')",
                        name="ck_construction_projects_work_type"),
        CheckConstraint("contract_type IN ('labour_only','labour_material')",
                        name="ck_construction_projects_contract_type"),
        CheckConstraint("contract_amount_minor >= 0", name="ck_construction_projects_amount_nonnegative"),
        CheckConstraint("currency = 'PKR'", name="ck_construction_projects_currency_pkr"),
        CheckConstraint("status IN ('lead','quoted','approved','in_progress','on_hold','completed','cancelled')",
                        name="ck_construction_projects_status"),
        CheckConstraint("progress_percent BETWEEN 0 AND 100", name="ck_construction_projects_progress"),
        Index("ix_construction_projects_org_status_created", "organization_id", "status", "created_at"),
        Index("ix_construction_projects_client", "client_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    client_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    work_type: Mapped[str] = mapped_column(String(40), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(24), nullable=False)
    contract_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    location_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="lead", nullable=False)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class ConstructionBoqItem(Base):
    """Tenant-scoped BOQ line using Decimal quantity and integer PKR minor units."""
    __tablename__ = "construction_boq_items"
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "organization_id"],
            ["construction_projects.id", "construction_projects.organization_id"],
            ondelete="RESTRICT", name="fk_construction_boq_project_org"),
        UniqueConstraint("project_id", "line_number", name="uq_construction_boq_project_line"),
        CheckConstraint("line_number BETWEEN 1 AND 10000", name="ck_construction_boq_line_number"),
        CheckConstraint("quantity > 0", name="ck_construction_boq_quantity_positive"),
        CheckConstraint("unit_rate_minor >= 0", name="ck_construction_boq_rate_nonnegative"),
        CheckConstraint("line_total_minor >= 0", name="ck_construction_boq_total_nonnegative"),
        Index("ix_construction_boq_org_project", "organization_id", "project_id", "line_number"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    item_name: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_rate_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ConstructionQuote(Base):
    """BOQ-based quote snapshot with a separately mutable lifecycle."""
    __tablename__ = "construction_quotes"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_construction_quotes_id_org"),
        UniqueConstraint("organization_id", "quote_no", name="uq_construction_quotes_org_quote_no"),
        ForeignKeyConstraint(["project_id", "organization_id"],
            ["construction_projects.id", "construction_projects.organization_id"],
            ondelete="RESTRICT", name="fk_construction_quotes_project_org"),
        CheckConstraint("status IN ('draft','sent','accepted','rejected','expired')",
                        name="ck_construction_quotes_status"),
        CheckConstraint("currency = 'PKR'", name="ck_construction_quotes_currency_pkr"),
        CheckConstraint("validity_days BETWEEN 1 AND 365", name="ck_construction_quotes_validity"),
        CheckConstraint("overhead_pct BETWEEN 0 AND 100", name="ck_construction_quotes_overhead_pct"),
        CheckConstraint("profit_pct BETWEEN 0 AND 100", name="ck_construction_quotes_profit_pct"),
        CheckConstraint("discount_pct BETWEEN 0 AND 100", name="ck_construction_quotes_discount_pct"),
        CheckConstraint("tax_pct BETWEEN 0 AND 100", name="ck_construction_quotes_tax_pct"),
        CheckConstraint("boq_subtotal_minor >= 0 AND overhead_amount_minor >= 0 AND profit_amount_minor >= 0",
                        name="ck_construction_quotes_base_amounts_nonnegative"),
        CheckConstraint("discount_amount_minor >= 0 AND tax_amount_minor >= 0 AND total_minor >= 0",
                        name="ck_construction_quotes_adjustments_nonnegative"),
        Index("ix_construction_quotes_org_project_created", "organization_id", "project_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    quote_no: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    boq_subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    overhead_pct: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"), nullable=False)
    overhead_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    profit_pct: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"), nullable=False)
    profit_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"), nullable=False)
    discount_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tax_pct: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"), nullable=False)
    tax_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    validity_days: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    terms: Mapped[str | None] = mapped_column(String(3000), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class ConstructionQuoteLine(Base):
    """Snapshot line copied from a tenant-owned BOQ; no live BOQ dependency."""
    __tablename__ = "construction_quote_lines"
    __table_args__ = (
        ForeignKeyConstraint(["quote_id", "organization_id"],
            ["construction_quotes.id", "construction_quotes.organization_id"],
            ondelete="RESTRICT", name="fk_construction_quote_lines_quote_org"),
        UniqueConstraint("quote_id", "line_number", name="uq_construction_quote_lines_quote_line"),
        CheckConstraint("line_number BETWEEN 1 AND 10000", name="ck_construction_quote_lines_line_number"),
        CheckConstraint("source_boq_line_number IS NULL OR source_boq_line_number BETWEEN 1 AND 10000",
                        name="ck_construction_quote_lines_source_line"),
        CheckConstraint("quantity > 0", name="ck_construction_quote_lines_quantity_positive"),
        CheckConstraint("unit_rate_minor >= 0 AND line_total_minor >= 0",
                        name="ck_construction_quote_lines_amounts_nonnegative"),
        Index("ix_construction_quote_lines_org_quote", "organization_id", "quote_id", "line_number"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    quote_id: Mapped[str] = mapped_column(String(36), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_boq_line_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    item_name: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_rate_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ConstructionCashbookEntry(Base):
    """Immutable project cashbook event linked to one balanced journal entry."""
    __tablename__ = "construction_cashbook_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["construction_projects.id", "construction_projects.organization_id"],
            ondelete="RESTRICT",
            name="fk_construction_cashbook_project_org",
        ),
        ForeignKeyConstraint(
            ["journal_entry_id", "organization_id"],
            ["journal_entries.id", "journal_entries.organization_id"],
            ondelete="RESTRICT",
            name="fk_construction_cashbook_journal_org",
        ),
        UniqueConstraint("journal_entry_id", name="uq_construction_cashbook_journal_entry"),
        CheckConstraint("direction IN ('income','expense')", name="ck_construction_cashbook_direction"),
        CheckConstraint(
            "amount_minor > 0 AND amount_minor <= 9000000000000000",
            name="ck_construction_cashbook_amount",
        ),
        CheckConstraint("currency = 'PKR'", name="ck_construction_cashbook_currency_pkr"),
        Index(
            "ix_construction_cashbook_org_project_created",
            "organization_id", "project_id", "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    journal_entry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ConstructionLabourEntry(Base):
    """Append-only, tenant-scoped labour register; worker names stay project-private."""
    __tablename__ = "construction_labour_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["construction_projects.id", "construction_projects.organization_id"],
            ondelete="RESTRICT",
            name="fk_construction_labour_project_org",
        ),
        CheckConstraint("length(trim(worker_name)) > 0", name="ck_construction_labour_worker_name"),
        CheckConstraint("worker_count BETWEEN 1 AND 10000", name="ck_construction_labour_worker_count"),
        CheckConstraint("days > 0 AND days <= 999999.99", name="ck_construction_labour_days"),
        CheckConstraint(
            "daily_rate_minor BETWEEN 0 AND 9000000000000000",
            name="ck_construction_labour_daily_rate",
        ),
        CheckConstraint(
            "total_amount_minor BETWEEN 0 AND 9000000000000000",
            name="ck_construction_labour_total",
        ),
        CheckConstraint("currency = 'PKR'", name="ck_construction_labour_currency_pkr"),
        Index(
            "ix_construction_labour_org_project_date",
            "organization_id", "project_id", "work_date",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    worker_name: Mapped[str] = mapped_column(String(140), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    worker_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    days: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), default=Decimal("1"), server_default="1", nullable=False
    )
    daily_rate_minor: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    total_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), default="PKR", server_default="PKR", nullable=False
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ConstructionMaterialEntry(Base):
    """Append-only, tenant-scoped material register using Decimal quantity and PKR minor units."""
    __tablename__ = "construction_material_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["construction_projects.id", "construction_projects.organization_id"],
            ondelete="RESTRICT",
            name="fk_construction_material_project_org",
        ),
        CheckConstraint("length(trim(item_name)) > 0", name="ck_construction_material_item_name"),
        CheckConstraint("length(trim(unit)) > 0", name="ck_construction_material_unit"),
        CheckConstraint("quantity > 0", name="ck_construction_material_quantity"),
        CheckConstraint(
            "unit_price_minor BETWEEN 0 AND 9000000000000000",
            name="ck_construction_material_unit_price",
        ),
        CheckConstraint(
            "total_amount_minor BETWEEN 0 AND 9000000000000000",
            name="ck_construction_material_total",
        ),
        CheckConstraint("currency = 'PKR'", name="ck_construction_material_currency_pkr"),
        Index(
            "ix_construction_material_org_project_date",
            "organization_id", "project_id", "entry_date",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    item_name: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    total_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    supplier: Mapped[str | None] = mapped_column(String(160), nullable=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), default="PKR", server_default="PKR", nullable=False
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class UserPhoneVerification(Base):
    """Trusted provider-issued phone verification snapshot; no user self-verification API exists."""
    __tablename__ = "user_phone_verifications"
    __table_args__ = (Index("ix_user_phone_verifications_phone", "phone_e164"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class PropertyListing(Base):
    """User-owned property listing; moderation never implies legal-title verification."""
    __tablename__ = "property_listings"
    __table_args__ = (
        CheckConstraint(
            "property_type IN ('plot','house','apartment','commercial','agricultural','other')",
            name="ck_property_listings_type",
        ),
        CheckConstraint("area_value > 0", name="ck_property_listings_area_positive"),
        CheckConstraint(
            "asking_price_minor > 0 AND asking_price_minor <= 9000000000000000",
            name="ck_property_listings_price",
        ),
        CheckConstraint(
            "listing_fee_minor > 0 AND listing_fee_minor <= 9000000000000000",
            name="ck_property_listings_fee",
        ),
        CheckConstraint("currency = 'PKR'", name="ck_property_listings_currency_pkr"),
        CheckConstraint(
            "dispute_status IN ('seller_declared_non_disputed','disputed','unknown')",
            name="ck_property_listings_dispute_status",
        ),
        CheckConstraint(
            "title_verification_status IN ('not_verified','under_review','rejected')",
            name="ck_property_listings_title_status",
        ),
        CheckConstraint(
            "listing_fee_status IN ('pending','paid','rejected')",
            name="ck_property_listings_fee_status",
        ),
        CheckConstraint(
            "listing_status IN ('pending_fee','pending_review','published','rejected','sold','withdrawn')",
            name="ck_property_listings_status",
        ),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_property_listings_coordinate_pair",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_property_listings_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_property_listings_longitude",
        ),
        Index(
            "ix_property_listings_public_search",
            "listing_status", "country", "state_region", "city", "property_type", "created_at",
        ),
        Index("ix_property_listings_seller_created", "seller_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    listing_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    seller_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    seller_phone: Mapped[str] = mapped_column(String(16), nullable=False)
    property_type: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str] = mapped_column(String(3000), default="", nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state_region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str] = mapped_column(String(100), default="Pakistan", nullable=False)
    locality: Mapped[str | None] = mapped_column(String(160), nullable=True)
    address_private: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    area_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    area_unit: Mapped[str] = mapped_column(String(24), nullable=False)
    asking_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    dispute_status: Mapped[str] = mapped_column(String(40), nullable=False)
    seller_declaration: Mapped[str] = mapped_column(String(500), nullable=False)
    title_verification_status: Mapped[str] = mapped_column(
        String(24), default="not_verified", nullable=False
    )
    listing_fee_minor: Mapped[int] = mapped_column(BigInteger, default=50_000, nullable=False)
    listing_fee_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    listing_status: Mapped[str] = mapped_column(String(24), default="pending_fee", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class PropertyTransaction(Base):
    """Bilateral property-sale confirmation; payment verification is a separate lifecycle."""
    __tablename__ = "property_transactions"
    __table_args__ = (
        UniqueConstraint("listing_id", "buyer_user_id", name="uq_property_transactions_listing_buyer"),
        CheckConstraint("buyer_user_id <> seller_user_id", name="ck_property_transactions_distinct_parties"),
        CheckConstraint(
            "status IN ('interest','awaiting_confirmation','sale_confirmed','commission_settled','cancelled','disputed')",
            name="ck_property_transactions_status",
        ),
        CheckConstraint(
            "final_sale_price_minor IS NULL OR "
            "(final_sale_price_minor > 0 AND final_sale_price_minor <= 9000000000000000)",
            name="ck_property_transactions_price",
        ),
        CheckConstraint(
            "buyer_commission_minor IS NULL OR buyer_commission_minor >= 0",
            name="ck_property_transactions_buyer_commission",
        ),
        CheckConstraint(
            "seller_commission_minor IS NULL OR seller_commission_minor >= 0",
            name="ck_property_transactions_seller_commission",
        ),
        CheckConstraint("currency = 'PKR'", name="ck_property_transactions_currency_pkr"),
        Index("ix_property_transactions_buyer_status", "buyer_user_id", "status", "created_at"),
        Index("ix_property_transactions_seller_status", "seller_user_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    transaction_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    listing_id: Mapped[str] = mapped_column(
        ForeignKey("property_listings.id", ondelete="RESTRICT"), nullable=False
    )
    buyer_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    seller_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    final_sale_price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    buyer_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    seller_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    buyer_commission_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    seller_commission_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="interest", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class PropertyPayment(Base):
    """Manual-review payment evidence; a submitted reference never marks payment verified."""
    __tablename__ = "property_payments"
    __table_args__ = (
        CheckConstraint(
            "payer_role IN ('seller','buyer')",
            name="ck_property_payments_payer_role",
        ),
        CheckConstraint(
            "payment_type IN ('listing_fee','buyer_commission','seller_commission')",
            name="ck_property_payments_type",
        ),
        CheckConstraint(
            "status IN ('pending','submitted','verified','rejected')",
            name="ck_property_payments_status",
        ),
        CheckConstraint("amount_minor > 0 AND amount_minor <= 9000000000000000",
                        name="ck_property_payments_amount"),
        CheckConstraint("currency = 'PKR'", name="ck_property_payments_currency_pkr"),
        CheckConstraint(
            "(payment_type = 'listing_fee' AND transaction_id IS NULL AND payer_role = 'seller') OR "
            "(payment_type = 'buyer_commission' AND transaction_id IS NOT NULL AND payer_role = 'buyer') OR "
            "(payment_type = 'seller_commission' AND transaction_id IS NOT NULL AND payer_role = 'seller')",
            name="ck_property_payments_type_party",
        ),
        Index("ix_property_payments_payer_status", "payer_user_id", "status", "created_at"),
        Index("ix_property_payments_status_created", "status", "created_at"),
        Index(
            "uq_property_listing_fee_payment", "listing_id", unique=True,
            sqlite_where=text("payment_type = 'listing_fee'"),
            postgresql_where=text("payment_type = 'listing_fee'"),
        ),
        Index(
            "uq_property_transaction_payment", "transaction_id", "payment_type", unique=True,
            sqlite_where=text("transaction_id IS NOT NULL"),
            postgresql_where=text("transaction_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    listing_id: Mapped[str] = mapped_column(
        ForeignKey("property_listings.id", ondelete="RESTRICT"), nullable=False
    )
    transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("property_transactions.id", ondelete="RESTRICT"), nullable=True
    )
    payer_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    payer_role: Mapped[str] = mapped_column(String(8), nullable=False)
    payment_type: Mapped[str] = mapped_column(String(24), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    payment_method: Mapped[str | None] = mapped_column(String(24), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reviewer_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EducationSettings(Base):
    """Owner-configured education commission policy; unset means paid courses fail closed."""
    __tablename__ = "education_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_education_settings_singleton"),
        CheckConstraint(
            "course_commission_bps IS NULL OR course_commission_bps BETWEEN 0 AND 10000",
            name="ck_education_settings_commission_bps",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    course_commission_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class EducationStudentProfile(Base):
    """Minimal student profile; minor consent is a separate reviewed record."""
    __tablename__ = "education_student_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_education_student_user"),
        CheckConstraint(
            "age_band IN ('under_18','adult','prefer_not_to_say')",
            name="ck_education_student_age_band",
        ),
        CheckConstraint("length(country_code) = 2", name="ck_education_student_country_code"),
        CheckConstraint(
            "age_verified = false OR age_band = 'adult'",
            name="ck_education_student_age_verified_adult",
        ),
        Index("ix_education_student_age_country", "age_band", "country_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(12), default="ur", nullable=False)
    age_band: Mapped[str] = mapped_column(String(24), nullable=False)
    age_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    age_verification_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    age_verified_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    age_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="PK", nullable=False)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class EducationGuardianConsent(Base):
    """A minor's guardian consent, verified separately by an authorized administrator."""
    __tablename__ = "education_guardian_consents"
    __table_args__ = (
        UniqueConstraint("student_profile_id", name="uq_education_guardian_student"),
        CheckConstraint(
            "status IN ('pending','verified','rejected')",
            name="ck_education_guardian_status",
        ),
        Index("ix_education_guardian_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    student_profile_id: Mapped[str] = mapped_column(
        ForeignKey("education_student_profiles.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    submission_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    verification_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reviewer_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class EducationTeacherProfile(Base):
    """Teacher listing profile; role fee and adult verification live in role entitlements."""
    __tablename__ = "education_teacher_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_education_teacher_user"),
        CheckConstraint(
            "verification_status IN ('pending','verified','rejected','suspended')",
            name="ck_education_teacher_status",
        ),
        CheckConstraint("length(country_code) = 2", name="ck_education_teacher_country_code"),
        Index("ix_education_teacher_status_country", "verification_status", "country_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    subjects: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    qualifications: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="PK", nullable=False)
    online_classes: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    verification_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reviewer_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class EducationCourse(Base):
    """Moderated course with a commission snapshot and integer PKR minor-unit pricing."""
    __tablename__ = "education_courses"
    __table_args__ = (
        CheckConstraint(
            "age_group IN ('under_18','adult','all')",
            name="ck_education_course_age_group",
        ),
        CheckConstraint(
            "course_type IN ('recorded','live','notes','hybrid')",
            name="ck_education_course_type",
        ),
        CheckConstraint(
            "status IN ('pending','published','rejected','suspended')",
            name="ck_education_course_status",
        ),
        CheckConstraint(
            "price_minor >= 0 AND price_minor <= 9000000000000000",
            name="ck_education_course_price",
        ),
        CheckConstraint("currency = 'PKR'", name="ck_education_course_currency_pkr"),
        CheckConstraint(
            "commission_bps BETWEEN 0 AND 10000",
            name="ck_education_course_commission_bps",
        ),
        Index("ix_education_course_discovery", "status", "country_code", "city", "subject", "created_at"),
        Index("ix_education_course_teacher_created", "teacher_profile_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    teacher_profile_id: Mapped[str] = mapped_column(
        ForeignKey("education_teacher_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(String(3000), default="", nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    language: Mapped[str] = mapped_column(String(12), default="ur", nullable=False)
    age_group: Mapped[str] = mapped_column(String(16), default="all", nullable=False)
    course_type: Mapped[str] = mapped_column(String(16), default="recorded", nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    commission_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), default="PK", nullable=False)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    reviewer_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class EducationCourseEnrollment(Base):
    """Per-student course purchase snapshot; submitted references are never payment proof."""
    __tablename__ = "education_course_enrollments"
    __table_args__ = (
        UniqueConstraint("course_id", "student_profile_id", name="uq_education_enrollment_course_student"),
        CheckConstraint(
            "payment_status IN ('pending','submitted','verified','rejected','refunded')",
            name="ck_education_enrollment_payment_status",
        ),
        CheckConstraint(
            "course_price_minor >= 0 AND commission_minor >= 0 AND teacher_amount_minor >= 0",
            name="ck_education_enrollment_amounts_nonnegative",
        ),
        CheckConstraint(
            "commission_minor + teacher_amount_minor = course_price_minor",
            name="ck_education_enrollment_amounts_balanced",
        ),
        CheckConstraint("currency = 'PKR'", name="ck_education_enrollment_currency_pkr"),
        CheckConstraint(
            "payment_method IS NULL OR payment_method IN ('bank_transfer','cash','manual')",
            name="ck_education_enrollment_payment_method",
        ),
        Index("ix_education_enrollment_student_created", "student_profile_id", "created_at"),
        Index("ix_education_enrollment_payment_status", "payment_status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    course_id: Mapped[str] = mapped_column(
        ForeignKey("education_courses.id", ondelete="RESTRICT"), nullable=False
    )
    student_profile_id: Mapped[str] = mapped_column(
        ForeignKey("education_student_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    course_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    commission_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    teacher_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    payment_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    payment_method: Mapped[str | None] = mapped_column(String(24), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    payment_verification_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reviewer_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class EducationCourseMaterial(Base):
    """Teacher-owned learning material held for independent moderation."""
    __tablename__ = "education_course_materials"
    __table_args__ = (
        CheckConstraint(
            "material_type IN ('notes','video_link','resource_link')",
            name="ck_education_material_type",
        ),
        CheckConstraint(
            "status IN ('pending','published','rejected')",
            name="ck_education_material_status",
        ),
        CheckConstraint(
            "resource_url IS NULL OR resource_url LIKE 'https://%'",
            name="ck_education_material_https_url",
        ),
        Index("ix_education_material_course_status", "course_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    course_id: Mapped[str] = mapped_column(
        ForeignKey("education_courses.id", ondelete="RESTRICT"), nullable=False
    )
    teacher_profile_id: Mapped[str] = mapped_column(
        ForeignKey("education_teacher_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    material_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content_text: Mapped[str | None] = mapped_column(String(12000), nullable=True)
    resource_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    reviewer_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class WorkforceProfile(Base):
    """Public workforce profile; role entitlement remains the authority for age and fees."""
    __tablename__ = "workforce_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_key", name="uq_workforce_user_role"),
        CheckConstraint(
            "role_key IN ('contractor','mason','labour')",
            name="ck_workforce_role_key",
        ),
        CheckConstraint(
            "experience_years >= 0 AND experience_years <= 70",
            name="ck_workforce_experience_range",
        ),
        CheckConstraint(
            "service_radius_km BETWEEN 1 AND 1000",
            name="ck_workforce_service_radius",
        ),
        CheckConstraint(
            "length(country_code) = 2",
            name="ck_workforce_country_code_length",
        ),
        CheckConstraint(
            "availability IN ('available','unavailable')",
            name="ck_workforce_availability",
        ),
        CheckConstraint(
            "moderation_status IN ('pending','published','rejected','suspended')",
            name="ck_workforce_moderation_status",
        ),
        Index(
            "ix_workforce_public_discovery",
            "role_key",
            "moderation_status",
            "availability",
            "country_code",
            "city",
        ),
        Index("ix_workforce_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_key: Mapped[str] = mapped_column(
        ForeignKey("role_definitions.role_key", ondelete="RESTRICT"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    specialty: Mapped[str] = mapped_column(String(120), nullable=False)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    experience_years: Mapped[Decimal] = mapped_column(
        Numeric(4, 1), default=Decimal("0.0"), nullable=False
    )
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="PK", nullable=False)
    service_radius_km: Mapped[int] = mapped_column(Integer, default=25, nullable=False)
    travels_for_work: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    available_cities: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    availability: Mapped[str] = mapped_column(
        String(16), default="available", nullable=False
    )
    moderation_status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False
    )
    reviewer_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class WorkforceServiceRequest(Base):
    """Client hiring request; verified phone is timestamped, never copied into this row."""
    __tablename__ = "workforce_service_requests"
    __table_args__ = (
        CheckConstraint(
            "service_type IN ('hire_worker','hire_contractor')",
            name="ck_workforce_service_request_type",
        ),
        CheckConstraint(
            "status IN ('new','matched','accepted','in_progress','completed','cancelled','disputed')",
            name="ck_workforce_service_request_status",
        ),
        CheckConstraint(
            "platform_fee_status IN ('pending','submitted','verified','rejected')",
            name="ck_workforce_service_request_fee_status",
        ),
        CheckConstraint("currency = 'PKR'", name="ck_workforce_service_request_currency"),
        CheckConstraint(
            "platform_fee_minor = 5000 AND platform_fee_currency = 'PKR'",
            name="ck_workforce_service_request_fee_snapshot",
        ),
        CheckConstraint(
            "budget_minor IS NULL OR budget_minor BETWEEN 0 AND 9000000000000000",
            name="ck_workforce_service_request_budget",
        ),
        CheckConstraint(
            "length(country_code) = 2",
            name="ck_workforce_service_request_country_code",
        ),
        CheckConstraint(
            "(provider_terms_version IS NULL AND provider_terms_accepted_at IS NULL) OR "
            "(provider_terms_version IS NOT NULL AND provider_terms_accepted_at IS NOT NULL)",
            name="ck_workforce_service_request_provider_terms_pair",
        ),
        UniqueConstraint("request_code", name="uq_workforce_service_request_code"),
        Index(
            "ix_workforce_service_request_client_created",
            "client_user_id",
            "created_at",
        ),
        Index(
            "ix_workforce_service_request_provider_status",
            "provider_profile_id",
            "status",
            "platform_fee_status",
        ),
        Index(
            "ix_workforce_service_request_fee_created",
            "platform_fee_status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    request_code: Mapped[str] = mapped_column(String(40), nullable=False)
    client_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    provider_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("workforce_profiles.id", ondelete="RESTRICT"), nullable=True
    )
    service_type: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    details: Mapped[str] = mapped_column(String(3000), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="PK", nullable=False)
    budget_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    client_phone_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    client_terms_version: Mapped[str] = mapped_column(String(48), nullable=False)
    client_terms_accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provider_terms_version: Mapped[str | None] = mapped_column(
        String(48), nullable=True
    )
    provider_terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False)
    platform_fee_minor: Mapped[int] = mapped_column(
        BigInteger, default=5000, nullable=False
    )
    platform_fee_currency: Mapped[str] = mapped_column(
        String(3), default="PKR", nullable=False
    )
    platform_fee_status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False
    )
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disputed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class WorkforceServicePayment(Base):
    """Manual evidence review for a workforce service fee; never a provider webhook."""
    __tablename__ = "workforce_service_payments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('submitted','verified','rejected')",
            name="ck_workforce_service_payment_status",
        ),
        CheckConstraint(
            "payment_method IN ('bank_transfer','cash_deposit')",
            name="ck_workforce_service_payment_method",
        ),
        CheckConstraint(
            "amount_minor = 5000 AND currency = 'PKR'",
            name="ck_workforce_service_payment_amount",
        ),
        Index(
            "uq_workforce_service_payment_active",
            "service_request_id",
            unique=True,
            sqlite_where=text("status IN ('submitted','verified')"),
            postgresql_where=text("status IN ('submitted','verified')"),
        ),
        Index(
            "ix_workforce_service_payment_request_created",
            "service_request_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    service_request_id: Mapped[str] = mapped_column(
        ForeignKey("workforce_service_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="PKR", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="submitted", nullable=False)
    payment_method: Mapped[str] = mapped_column(String(24), nullable=False)
    payment_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    reviewer_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# Register the workforce contact-relay model for Alembic metadata and create_all.
import app.workforce_contact_models  # noqa: F401,E402
