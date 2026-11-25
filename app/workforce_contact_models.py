"""Internal workforce service-request message records; no contact fields are stored."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
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
            "moderation_status IN ('visible','hidden')",
            name="ck_workforce_service_message_moderation_status",
        ),
        CheckConstraint(
            "length(body) BETWEEN 1 AND 16384",
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
    moderation_status: Mapped[str] = mapped_column(
        String(16), default="visible", server_default="visible", nullable=False
    )
    body: Mapped[str] = mapped_column(EncryptedMessageText(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class WorkforceMessageReport(Base):
    """Participant report referencing a message without copying its body or contact data."""

    __tablename__ = "workforce_message_reports"
    __table_args__ = (
        CheckConstraint(
            "reason_code IN ('spam','harassment','contact_sharing','scam_or_fraud','unsafe_content','other')",
            name="ck_workforce_message_report_reason",
        ),
        CheckConstraint(
            "status IN ('open','dismissed','actioned')",
            name="ck_workforce_message_report_status",
        ),
        UniqueConstraint(
            "message_id",
            "reporter_user_id",
            name="uq_workforce_message_reporter_message",
        ),
        Index("ix_workforce_message_reports_status_created", "status", "created_at"),
        Index("ix_workforce_message_reports_message_created", "message_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("workforce_service_messages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reporter_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="open", server_default="open", nullable=False
    )
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )



class WorkforceMessageAppeal(Base):
    """A bounded appeal by the original sender of a moderator-hidden message."""

    __tablename__ = "workforce_message_appeals"
    __table_args__ = (
        CheckConstraint(
            "reason_code IN ('decision_incorrect','context_missing','identity_misuse','other')",
            name="ck_workforce_message_appeal_reason",
        ),
        CheckConstraint(
            "status IN ('open','upheld','denied')",
            name="ck_workforce_message_appeal_status",
        ),
        CheckConstraint(
            "status = 'open' OR reviewed_at IS NOT NULL",
            name="ck_workforce_message_appeal_reviewed_at",
        ),
        UniqueConstraint(
            "message_id",
            "appellant_user_id",
            name="uq_workforce_message_appeal_message_appellant",
        ),
        Index("ix_workforce_message_appeals_status_created", "status", "created_at"),
        Index("ix_workforce_message_appeals_message_created", "message_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("workforce_service_messages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    appellant_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="open", server_default="open", nullable=False
    )
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
