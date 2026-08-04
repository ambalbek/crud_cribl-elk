import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, String, DateTime
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship

from app.extensions import db


class RequestStatus(str, enum.Enum):
    intake_pending = "intake_pending"
    intake_validated = "intake_validated"
    engagement = "engagement"
    solutioning = "solutioning"
    delivery_collection = "delivery_collection"
    delivery_routing = "delivery_routing"
    delivery_storage = "delivery_storage"
    delivery_complete = "delivery_complete"
    validation = "validation"
    complete = "complete"
    cancelled = "cancelled"


class OnboardingRequest(db.Model):
    __tablename__ = "onboarding_requests"

    id = db.Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    app_name = db.Column(String(256), nullable=False)
    apm_id = db.Column(String(128), unique=True, nullable=False)
    requestor_name = db.Column(String(256), nullable=False)
    requestor_email = db.Column(String(320), nullable=False)
    team = db.Column(String(256), nullable=False)
    environment = db.Column(
        Enum("dev", "stage", "prod", name="environment_enum", create_constraint=True),
        nullable=False,
    )
    status = db.Column(
        Enum(RequestStatus, name="request_status_enum", create_constraint=True),
        nullable=False,
        default=RequestStatus.intake_pending,
    )
    form_data = db.Column(JSONB, nullable=True, default=dict)
    entity_mapping = db.Column(JSONB, nullable=True, default=dict)
    workbook_data = db.Column(JSONB, nullable=True, default=dict)
    created_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    audit_logs = relationship(
        "AuditLog", back_populates="request", cascade="all, delete-orphan"
    )
    delivery_jobs = relationship(
        "DeliveryJob", back_populates="request", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<OnboardingRequest {self.apm_id} [{self.status.value}]>"
