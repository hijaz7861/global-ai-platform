"""Request and response schemas for identity, commerce, AI, and accounting."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    totp_code: str | None = Field(default=None, pattern=r"^\d{6}$")
    recovery_code: str | None = Field(default=None, min_length=16, max_length=32)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class MFAEnrollRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class MFAConfirmRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class MFADisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    code: str | None = Field(default=None, pattern=r"^\d{6}$")
    recovery_code: str | None = Field(default=None, min_length=16, max_length=32)

    @model_validator(mode="after")
    def require_one_mfa_proof(self):
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("Provide exactly one of code or recovery_code")
        return self


class MFARecoveryCodesRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(pattern=r"^\d{6}$")


class MFARecoveryCodesResponse(BaseModel):
    codes: list[str]


class MFAEnrollmentResponse(BaseModel):
    secret: str
    otpauth_uri: str
    expires_in_seconds: int


class EmailVerificationConfirm(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)

    @field_validator("name")
    @classmethod
    def require_nonblank_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Organization name must contain at least two non-space characters")
        return normalized


class OrganizationResponse(BaseModel):
    id: str
    name: str
    role: str

    model_config = ConfigDict(from_attributes=True)


class MembershipInvitationCreate(BaseModel):
    email: EmailStr
    role: Literal["member", "admin"] = "member"

    @field_validator("email")
    @classmethod
    def normalize_invitation_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class MembershipInvitationAccept(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class MembershipInvitationResponse(BaseModel):
    id: str
    organization_id: str
    email: EmailStr
    role: str
    expires_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MembershipCreate(BaseModel):
    email: EmailStr
    role: Literal["member", "admin"] = "member"


class MembershipResponse(BaseModel):
    user_id: str
    email: EmailStr
    role: str
    created_at: datetime


class ListingCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=5000)
    price_minor: int = Field(gt=0, le=9_000_000_000_000_000)
    currency: str = Field(min_length=3, max_length=3)
    inventory_quantity: int = Field(default=0, ge=0, le=2_000_000_000)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Title must contain at least two non-space characters")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha() or not value.isascii():
            raise ValueError("Currency must be a three-letter ASCII code")
        return value


class ListingResponse(BaseModel):
    id: str
    organization_id: str
    title: str
    description: str
    price_minor: int
    currency: str
    status: str
    inventory_quantity: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InventoryAdjustmentRequest(BaseModel):
    delta: int = Field(ge=-2_000_000_000, le=2_000_000_000)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def reject_zero_adjustment(self):
        if self.delta == 0:
            raise ValueError("Inventory adjustment must not be zero")
        return self


class InventoryAdjustmentResponse(BaseModel):
    id: str
    organization_id: str
    listing_id: str
    actor_user_id: str
    delta: int
    quantity_before: int
    quantity_after: int
    reason: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderLineCreate(BaseModel):
    listing_id: str = Field(min_length=1, max_length=36)
    quantity: int = Field(gt=0, le=1000)


class OrderCreate(BaseModel):
    listing_id: str | None = Field(default=None, min_length=1, max_length=36)
    quantity: int | None = Field(default=None, gt=0, le=1000)
    items: list[OrderLineCreate] | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_order_lines(self):
        if self.items is not None:
            if self.listing_id is not None or self.quantity is not None:
                raise ValueError("Use either the single-item fields or items, not both")
            listing_ids = [item.listing_id for item in self.items]
            if len(set(listing_ids)) != len(listing_ids):
                raise ValueError("Each listing may appear only once in an order")
        elif self.listing_id is None or self.quantity is None:
            raise ValueError("Provide listing_id and quantity, or a non-empty items list")
        return self

    def normalized_items(self) -> list[tuple[str, int]]:
        if self.items is not None:
            return sorted((item.listing_id, item.quantity) for item in self.items)
        assert self.listing_id is not None and self.quantity is not None
        return [(self.listing_id, self.quantity)]


class OrderItemResponse(BaseModel):
    listing_id: str
    title: str
    quantity: int
    unit_price_minor: int
    line_total_minor: int


class OrderResponse(BaseModel):
    id: str
    status: str
    total_minor: int
    currency: str
    created_at: datetime
    expires_at: datetime
    items: list[OrderItemResponse]


class OrderExpirySweepResponse(BaseModel):
    expired_count: int
    order_ids: list[str]


class SellerOrderItemResponse(BaseModel):
    listing_id: str
    title: str
    quantity: int
    unit_price_minor: int
    line_total_minor: int


class SellerOrderResponse(BaseModel):
    id: str
    status: str
    currency: str
    created_at: datetime
    subtotal_minor: int
    items: list[SellerOrderItemResponse]


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    max_output_tokens: int = Field(default=256, ge=1, le=2048)

    @field_validator("prompt")
    @classmethod
    def require_nonblank_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Prompt must not be blank")
        return normalized


class ChatResponse(BaseModel):
    request_id: str
    provider: str
    model: str
    content: str
    input_tokens: int
    output_tokens: int


class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=2, max_length=200)
    account_type: Literal["asset", "liability", "equity", "revenue", "expense"]
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized or not normalized.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Account code may contain letters, digits, hyphens, and underscores")
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_account_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Account name must contain at least two non-space characters")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_account_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("Currency must be a three-letter ASCII code")
        return normalized


class AccountResponse(BaseModel):
    id: str
    organization_id: str
    code: str
    name: str
    account_type: str
    currency: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AccountingPeriodCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    starts_on: date
    ends_on: date

    @field_validator("name")
    @classmethod
    def normalize_period_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Period name must contain at least two non-space characters")
        return normalized

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.ends_on < self.starts_on:
            raise ValueError("Period end date must not precede its start date")
        return self


class AccountingPeriodResponse(BaseModel):
    id: str
    organization_id: str
    name: str
    starts_on: date
    ends_on: date
    status: str
    created_at: datetime
    closed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class JournalLineCreate(BaseModel):
    account_id: str = Field(min_length=1, max_length=36)
    debit_minor: int = Field(default=0, ge=0, le=9_000_000_000_000_000)
    credit_minor: int = Field(default=0, ge=0, le=9_000_000_000_000_000)

    @model_validator(mode="after")
    def exactly_one_side(self):
        if (self.debit_minor > 0) == (self.credit_minor > 0):
            raise ValueError("Each journal line must have either a positive debit or a positive credit")
        return self


class JournalEntryCreate(BaseModel):
    description: str = Field(min_length=2, max_length=500)
    lines: list[JournalLineCreate] = Field(min_length=2, max_length=100)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Description must contain at least two non-space characters")
        return normalized

    @model_validator(mode="after")
    def require_balanced_positive_totals(self):
        debit_total = sum(line.debit_minor for line in self.lines)
        credit_total = sum(line.credit_minor for line in self.lines)
        if debit_total <= 0 or debit_total != credit_total:
            raise ValueError("Journal debits and credits must balance to a positive amount")
        return self


class JournalLineResponse(BaseModel):
    account_id: str
    debit_minor: int
    credit_minor: int


class JournalEntryResponse(BaseModel):
    id: str
    description: str
    currency: str
    total_minor: int
    status: str
    created_at: datetime
    lines: list[JournalLineResponse]


class AccountLedgerEntryResponse(BaseModel):
    journal_entry_id: str
    created_at: datetime
    description: str
    debit_minor: int
    credit_minor: int
    running_balance_minor: int


class AccountLedgerResponse(BaseModel):
    account_id: str
    code: str
    name: str
    account_type: str
    currency: str
    start_date: date | None
    end_date: date | None
    opening_balance_minor: int
    closing_balance_minor: int
    entries: list[AccountLedgerEntryResponse]
    has_more: bool
    next_cursor_created_at: datetime | None
    next_cursor_entry_id: str | None


class TrialBalanceLineResponse(BaseModel):
    account_id: str
    code: str
    name: str
    account_type: str
    currency: str
    debit_total_minor: int
    credit_total_minor: int
    normal_balance_minor: int


class PasswordResetRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_reset_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=12, max_length=128)


class SecurityCleanupResponse(BaseModel):
    email_verification_tokens_deleted: int
    password_reset_tokens_deleted: int
    membership_invitations_deleted: int
    expired_access_revocations_deleted: int
    expired_refresh_families_deleted: int
    auth_attempts_deleted: int
    rate_limit_locks_deleted: int


class ProcurementRequestCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=3000)
    quantity: int = Field(gt=0, le=2_000_000_000)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("title")
    @classmethod
    def normalize_procurement_title(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Title must contain at least two non-space characters")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_procurement_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("Currency must be a three-letter ASCII code")
        return normalized


class ProcurementRequestResponse(BaseModel):
    id: str
    buyer_organization_id: str
    created_by_user_id: str
    title: str
    description: str
    quantity: int
    currency: str
    status: str
    awarded_quote_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SupplierQuoteCreate(BaseModel):
    unit_price_minor: int = Field(gt=0, le=9_000_000_000_000_000)
    currency: str = Field(min_length=3, max_length=3)
    lead_time_days: int = Field(ge=0, le=3650)
    notes: str = Field(default="", max_length=2000)

    @field_validator("currency")
    @classmethod
    def normalize_quote_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("Currency must be a three-letter ASCII code")
        return normalized


class SupplierQuoteResponse(BaseModel):
    id: str
    procurement_request_id: str
    supplier_organization_id: str
    created_by_user_id: str
    unit_price_minor: int
    currency: str
    lead_time_days: int
    notes: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProcurementAwardRequest(BaseModel):
    quote_id: str = Field(min_length=1, max_length=36)


class SupplierProfileCreate(BaseModel):
    legal_name: str = Field(min_length=2, max_length=200)
    business_type: Literal["manufacturer", "distributor", "wholesaler", "retailer", "service_provider", "other"]
    country_code: str = Field(min_length=2, max_length=2)
    contact_email: EmailStr
    website_url: str | None = Field(default=None, max_length=2048)
    description: str = Field(default="", max_length=2000)

    @field_validator("legal_name")
    @classmethod
    def normalize_legal_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Legal name must contain at least two non-space characters")
        return value

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 2 or not value.isascii() or not value.isalpha():
            raise ValueError("Country code must be a two-letter ASCII code")
        return value

    @field_validator("contact_email")
    @classmethod
    def normalize_contact_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("website_url")
    @classmethod
    def validate_website_url(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value.startswith(("https://", "http://")):
                raise ValueError("Website URL must use HTTP or HTTPS")
        return value


class SupplierProfileResponse(BaseModel):
    id: str
    organization_id: str
    legal_name: str
    business_type: str
    country_code: str
    contact_email: EmailStr
    website_url: str | None
    description: str
    status: str
    reviewer_notes: str | None
    created_by_user_id: str
    reviewed_by_user_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SupplierReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    notes: str = Field(default="", max_length=2000)

    @field_validator("notes")
    @classmethod
    def normalize_review_notes(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def require_rejection_reason(self):
        if self.decision == "rejected" and len(self.notes) < 3:
            raise ValueError("A rejection reason of at least three characters is required")
        return self
