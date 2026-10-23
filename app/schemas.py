"""Request and response schemas for identity, organizations, catalog, and orders."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


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
