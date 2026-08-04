import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, DateTime, Enum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.onboarding_request import RequestStatus


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    request_id = db.Column(
        PG_UUID(as_uuid=True),
        ForeignKey("onboarding_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = db.Column(
        Enum(RequestStatus, name="request_status_enum", create_constraint=False),
        nullable=False,
    )
    action = db.Column(String(256), nullable=False)
    actor = db.Column(String(256), nullable=False)
    outcome = db.Column(String(256), nullable=False)
    metadata_ = db.Column("metadata", JSONB, nullable=True, default=dict)
    created_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    request = relationship("OnboardingRequest", back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} by {self.actor} [{self.stage.value}]>"
