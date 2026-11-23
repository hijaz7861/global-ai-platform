"""Internal workforce service-request message records; no contact fields are stored."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utc_now
from app.security.encrypted_message import EncryptedMessageText


class WorkforceServiceMessage(Base):
    """Participant-only message with no phone, email, or external-contact columns."""

    __tablename__ = "workforce_service_messages"
    __table_args__ = (
        CheckConstraint(
            "sender_side IN ('client','provider')",
            name="ck_workforce_service_message_sender_side",
        ),
        CheckConstraint(
            "length(body) BETWEEN 1 AND 4096",
            name="ck_workforce_service_message_body_length",
        ),
        Index(
            "ix_workforce_service_message_request_created",
            "service_request_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_workforce_service_message_sender_created",
            "sender_user_id",
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
    sender_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sender_side: Mapped[str] = mapped_column(String(12), nullable=False)
    body: Mapped[str] = mapped_column(EncryptedMessageText(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
