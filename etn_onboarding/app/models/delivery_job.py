import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, DateTime, Enum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship

from app.extensions import db


class DeliveryJob(db.Model):
    __tablename__ = "delivery_jobs"

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
    job_type = db.Column(
        Enum(
            "cribl_edge",
            "etn_portal",
            "harness_blob",
            name="job_type_enum",
            create_constraint=True,
        ),
        nullable=False,
    )
    status = db.Column(
        Enum(
            "pending",
            "running",
            "success",
            "failed",
            name="job_status_enum",
            create_constraint=True,
        ),
        nullable=False,
        default="pending",
    )
    external_ref = db.Column(String(512), nullable=True)
    started_at = db.Column(DateTime(timezone=True), nullable=True)
    completed_at = db.Column(DateTime(timezone=True), nullable=True)
    result = db.Column(JSONB, nullable=True, default=dict)

    request = relationship("OnboardingRequest", back_populates="delivery_jobs")

    def __repr__(self) -> str:
        return f"<DeliveryJob {self.job_type} [{self.status}]>"
