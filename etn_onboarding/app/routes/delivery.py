import logging
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify

from app.extensions import db
from app.models import OnboardingRequest, RequestStatus, DeliveryJob
from app.services.state_machine import transition_request, InvalidTransitionError
import app.services as services

logger = logging.getLogger(__name__)

delivery_bp = Blueprint("delivery", __name__, url_prefix="/api/delivery")


def _get_request_or_404(request_id):
    """Retrieve an onboarding request by ID or return a 404 response tuple."""
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return None
    return onboarding_req


@delivery_bp.route("/<uuid:request_id>/collection", methods=["POST"])
def trigger_collection(request_id):
    """Trigger a Cribl Edge configuration job and transition to delivery_collection.

    Creates a ``DeliveryJob`` of type ``cribl_edge`` and invokes the
    ``cribl_client`` service stub.
    """
    onboarding_req = _get_request_or_404(request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True) or {}
    actor = body.get("actor", "system")

    try:
        audit_entry = transition_request(
            onboarding_req,
            RequestStatus.delivery_collection,
            actor=actor,
            action="trigger_collection",
        )
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409

    job = DeliveryJob(
        request_id=onboarding_req.id,
        job_type="cribl_edge",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.session.add(job)
    db.session.flush()

    try:
        result = services.cribl.configure_edge_agent(
            app_name=onboarding_req.app_name,
            apm_id=onboarding_req.apm_id,
            config={"environment": onboarding_req.environment, "entity_mapping": onboarding_req.entity_mapping},
        )
        job.status = "success"
        job.result = result
        job.completed_at = datetime.now(timezone.utc)
    except Exception as exc:
        logger.exception("Cribl Edge config failed: request_id=%s", request_id)
        job.status = "failed"
        job.result = {"error": str(exc)}
        job.completed_at = datetime.now(timezone.utc)

    db.session.commit()

    logger.info("Collection job completed: request_id=%s job_id=%s status=%s", request_id, job.id, job.status)

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "job_id": str(job.id),
        "job_status": job.status,
        "audit_id": str(audit_entry.id),
    }), 200


@delivery_bp.route("/<uuid:request_id>/routing", methods=["POST"])
def trigger_routing(request_id):
    """Trigger an ETN Portal routing job and transition to delivery_routing.

    Creates a ``DeliveryJob`` of type ``etn_portal`` and invokes the
    ``etn_portal_client`` service stub.
    """
    onboarding_req = _get_request_or_404(request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True) or {}
    actor = body.get("actor", "system")

    try:
        audit_entry = transition_request(
            onboarding_req,
            RequestStatus.delivery_routing,
            actor=actor,
            action="trigger_routing",
        )
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409

    job = DeliveryJob(
        request_id=onboarding_req.id,
        job_type="etn_portal",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.session.add(job)
    db.session.flush()

    try:
        result = services.etn_portal.trigger_cribl_config(
            apm_id=onboarding_req.apm_id,
            app_name=onboarding_req.app_name,
            environment=onboarding_req.environment,
            config={"entity_mapping": onboarding_req.entity_mapping},
        )
        job.status = "success"
        job.result = result
        job.completed_at = datetime.now(timezone.utc)
    except Exception as exc:
        logger.exception("ETN Portal routing failed: request_id=%s", request_id)
        job.status = "failed"
        job.result = {"error": str(exc)}
        job.completed_at = datetime.now(timezone.utc)

    db.session.commit()

    logger.info("Routing job completed: request_id=%s job_id=%s status=%s", request_id, job.id, job.status)

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "job_id": str(job.id),
        "job_status": job.status,
        "audit_id": str(audit_entry.id),
    }), 200


@delivery_bp.route("/<uuid:request_id>/storage", methods=["POST"])
def trigger_storage(request_id):
    """Trigger a Harness blob storage job and transition to delivery_storage.

    Creates a ``DeliveryJob`` of type ``harness_blob`` and invokes the
    ``harness_client`` service stub.
    """
    onboarding_req = _get_request_or_404(request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True) or {}
    actor = body.get("actor", "system")

    try:
        audit_entry = transition_request(
            onboarding_req,
            RequestStatus.delivery_storage,
            actor=actor,
            action="trigger_storage",
        )
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409

    job = DeliveryJob(
        request_id=onboarding_req.id,
        job_type="harness_blob",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.session.add(job)
    db.session.flush()

    try:
        result = services.harness.trigger_blob_storage(
            apm_id=onboarding_req.apm_id,
            app_name=onboarding_req.app_name,
            environment=onboarding_req.environment,
        )
        job.status = "success"
        job.result = result
        job.completed_at = datetime.now(timezone.utc)
    except Exception as exc:
        logger.exception("Harness blob provisioning failed: request_id=%s", request_id)
        job.status = "failed"
        job.result = {"error": str(exc)}
        job.completed_at = datetime.now(timezone.utc)

    db.session.commit()

    logger.info("Storage job completed: request_id=%s job_id=%s status=%s", request_id, job.id, job.status)

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "job_id": str(job.id),
        "job_status": job.status,
        "audit_id": str(audit_entry.id),
    }), 200


@delivery_bp.route("/<uuid:request_id>/complete", methods=["POST"])
def mark_delivery_complete(request_id):
    """Mark all delivery phases as done and transition to delivery_complete.

    Verifies that all delivery jobs for this request have completed before
    allowing the transition.
    """
    onboarding_req = _get_request_or_404(request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True) or {}
    actor = body.get("actor", "system")

    # Verify all delivery jobs have finished
    pending_jobs = DeliveryJob.query.filter(
        DeliveryJob.request_id == onboarding_req.id,
        DeliveryJob.status.in_(["pending", "running"]),
    ).count()

    if pending_jobs > 0:
        return jsonify({
            "error": "Cannot complete delivery while jobs are still running",
            "pending_jobs": pending_jobs,
        }), 409

    try:
        audit_entry = transition_request(
            onboarding_req,
            RequestStatus.delivery_complete,
            actor=actor,
            action="mark_delivery_complete",
        )
        db.session.commit()
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409

    logger.info("Delivery marked complete: request_id=%s", request_id)

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "audit_id": str(audit_entry.id),
    }), 200
