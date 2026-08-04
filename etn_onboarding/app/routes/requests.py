import logging

from flask import Blueprint, request, jsonify

from app.extensions import db
from app.models import OnboardingRequest, RequestStatus
from app.services.state_machine import transition_request, InvalidTransitionError

logger = logging.getLogger(__name__)

requests_bp = Blueprint("requests", __name__, url_prefix="/api/requests")


@requests_bp.route("/<uuid:request_id>", methods=["GET"])
def get_request(request_id):
    """Get a single onboarding request with its audit log and delivery jobs."""
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    audit_logs = [
        {
            "id": str(log.id),
            "stage": log.stage.value,
            "action": log.action,
            "actor": log.actor,
            "outcome": log.outcome,
            "metadata": log.metadata_,
            "created_at": log.created_at.isoformat(),
        }
        for log in onboarding_req.audit_logs
    ]

    delivery_jobs = [
        {
            "id": str(job.id),
            "job_type": job.job_type,
            "status": job.status,
            "external_ref": job.external_ref,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "result": job.result,
        }
        for job in onboarding_req.delivery_jobs
    ]

    return jsonify({
        "id": str(onboarding_req.id),
        "app_name": onboarding_req.app_name,
        "apm_id": onboarding_req.apm_id,
        "requestor_name": onboarding_req.requestor_name,
        "requestor_email": onboarding_req.requestor_email,
        "team": onboarding_req.team,
        "environment": onboarding_req.environment,
        "status": onboarding_req.status.value,
        "form_data": onboarding_req.form_data,
        "entity_mapping": onboarding_req.entity_mapping,
        "workbook_data": onboarding_req.workbook_data,
        "created_at": onboarding_req.created_at.isoformat(),
        "updated_at": onboarding_req.updated_at.isoformat(),
        "audit_logs": audit_logs,
        "delivery_jobs": delivery_jobs,
    }), 200


@requests_bp.route("/<uuid:request_id>/transition", methods=["POST"])
def generic_transition(request_id):
    """Generic state transition endpoint.

    Accepts a JSON body with:
      - target_status (str, required) -- the status to transition to
      - actor (str, optional, defaults to 'system')
      - action (str, optional) -- description of the transition
      - metadata (dict, optional) -- extra context for the audit log
    """
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON"}), 400

    target_status_str = body.get("target_status")
    if not target_status_str:
        return jsonify({"error": "target_status is required"}), 400

    try:
        target_status = RequestStatus(target_status_str)
    except ValueError:
        valid = [s.value for s in RequestStatus]
        return jsonify({"error": f"Invalid target_status '{target_status_str}'", "valid_statuses": valid}), 400

    actor = body.get("actor", "system")
    action = body.get("action")
    metadata = body.get("metadata")

    try:
        audit_entry = transition_request(
            onboarding_req,
            target_status,
            actor=actor,
            action=action,
            metadata=metadata,
        )
        db.session.commit()
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409

    logger.info(
        "Generic transition: request_id=%s %s -> %s actor=%s",
        request_id, audit_entry.action, target_status.value, actor,
    )

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "audit_id": str(audit_entry.id),
    }), 200
