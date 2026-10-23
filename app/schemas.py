"""Request and response schemas for identity, commerce, orders, and accounting."""
from datetime import datetime
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

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


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

    @model_validator(mode="after")
    def reject_zero_adjustment(self):
        if self.delta == 0:
            raise ValueError("Inventory adjustment must not be zero")
        return self


class OrderCreate(BaseModel):
    listing_id: str = Field(min_length=1, max_length=36)
    quantity: int = Field(gt=0, le=1000)


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
    items: list[OrderItemResponse]


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
