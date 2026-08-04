"""Initial schema: onboarding_requests, audit_logs, delivery_jobs

Revision ID: 001_initial
Revises:
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Enum types ---
    environment_enum = sa.Enum(
        "dev", "stage", "prod",
        name="environment_enum",
    )
    environment_enum.create(op.get_bind(), checkfirst=True)

    request_status_enum = sa.Enum(
        "intake_pending",
        "intake_validated",
        "engagement",
        "solutioning",
        "delivery_collection",
        "delivery_routing",
        "delivery_storage",
        "delivery_complete",
        "validation",
        "complete",
        "cancelled",
        name="request_status_enum",
    )
    request_status_enum.create(op.get_bind(), checkfirst=True)

    job_type_enum = sa.Enum(
        "cribl_edge", "etn_portal", "harness_blob",
        name="job_type_enum",
    )
    job_type_enum.create(op.get_bind(), checkfirst=True)

    job_status_enum = sa.Enum(
        "pending", "running", "success", "failed",
        name="job_status_enum",
    )
    job_status_enum.create(op.get_bind(), checkfirst=True)

    # --- onboarding_requests ---
    op.create_table(
        "onboarding_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("app_name", sa.String(256), nullable=False),
        sa.Column("apm_id", sa.String(128), unique=True, nullable=False),
        sa.Column("requestor_name", sa.String(256), nullable=False),
        sa.Column("requestor_email", sa.String(320), nullable=False),
        sa.Column("team", sa.String(256), nullable=False),
        sa.Column(
            "environment",
            environment_enum,
            nullable=False,
        ),
        sa.Column(
            "status",
            request_status_enum,
            nullable=False,
            server_default="intake_pending",
        ),
        sa.Column("form_data", JSONB, nullable=True),
        sa.Column("entity_mapping", JSONB, nullable=True),
        sa.Column("workbook_data", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # --- audit_logs ---
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            UUID(as_uuid=True),
            sa.ForeignKey("onboarding_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("stage", request_status_enum, nullable=False),
        sa.Column("action", sa.String(256), nullable=False),
        sa.Column("actor", sa.String(256), nullable=False),
        sa.Column("outcome", sa.String(256), nullable=False),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # --- delivery_jobs ---
    op.create_table(
        "delivery_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            UUID(as_uuid=True),
            sa.ForeignKey("onboarding_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("job_type", job_type_enum, nullable=False),
        sa.Column(
            "status",
            job_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("external_ref", sa.String(512), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("delivery_jobs")
    op.drop_table("audit_logs")
    op.drop_table("onboarding_requests")

    sa.Enum(name="job_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="job_type_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="request_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="environment_enum").drop(op.get_bind(), checkfirst=True)
