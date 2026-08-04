import logging

from flask import Blueprint, request, jsonify

from app.extensions import db
from app.models import OnboardingRequest, RequestStatus, AuditLog
from app.services.state_machine import transition_request, InvalidTransitionError

logger = logging.getLogger(__name__)

validation_bp = Blueprint("validation", __name__, url_prefix="/api/validation")


@validation_bp.route("/<uuid:request_id>/turnover", methods=["POST"])
def record_turnover(request_id):
    """Record a turnover/dashboard handoff.

    Transitions the request to validation and logs the handoff details.

    Expects JSON body with:
      - actor (str, optional)
      - dashboard_url (str, optional) -- link to the handed-off dashboard
      - notes (str, optional)
    """
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True) or {}
    actor = body.get("actor", "system")

    try:
        audit_entry = transition_request(
            onboarding_req,
            RequestStatus.validation,
            actor=actor,
            action="record_turnover",
            metadata={
                "dashboard_url": body.get("dashboard_url", ""),
                "notes": body.get("notes", ""),
            },
        )
        db.session.commit()
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409

    logger.info("Turnover recorded: request_id=%s actor=%s", request_id, actor)

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "audit_id": str(audit_entry.id),
    }), 200


@validation_bp.route("/<uuid:request_id>/complete", methods=["POST"])
def mark_complete(request_id):
    """Mark the onboarding as complete after customer demo/validation.

    Transitions the request to the ``complete`` terminal state.

    Expects optional JSON body with:
      - actor (str, optional)
      - notes (str, optional)
    """
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True) or {}
    actor = body.get("actor", "system")

    try:
        audit_entry = transition_request(
            onboarding_req,
            RequestStatus.complete,
            actor=actor,
            action="mark_complete",
            metadata={"notes": body.get("notes", "")},
        )
        db.session.commit()
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409

    logger.info("Onboarding marked complete: request_id=%s actor=%s", request_id, actor)

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "audit_id": str(audit_entry.id),
    }), 200
