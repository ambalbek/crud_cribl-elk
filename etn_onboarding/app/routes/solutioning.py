import logging

from flask import Blueprint, request, jsonify

from app.extensions import db
from app.models import OnboardingRequest, RequestStatus
from app.services.state_machine import transition_request, InvalidTransitionError

logger = logging.getLogger(__name__)

solutioning_bp = Blueprint("solutioning", __name__, url_prefix="/api/solutioning")


@solutioning_bp.route("/<uuid:request_id>/advance", methods=["POST"])
def advance_to_solutioning(request_id):
    """Transition the request into the solutioning stage.

    Expects optional JSON body with ``actor`` (defaults to ``"system"``).
    """
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True) or {}
    actor = body.get("actor", "system")

    try:
        audit_entry = transition_request(
            onboarding_req,
            RequestStatus.solutioning,
            actor=actor,
            action="advance_to_solutioning",
        )
        db.session.commit()
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409

    logger.info("Advanced to solutioning: request_id=%s", request_id)

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "audit_id": str(audit_entry.id),
    }), 200


@solutioning_bp.route("/<uuid:request_id>/mapping", methods=["PUT"])
def save_entity_mapping(request_id):
    """Save or update the entity/field mapping for an onboarding request.

    Expects a JSON body representing the mapping structure.  The entire body
    is persisted to ``entity_mapping``.
    """
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    onboarding_req.entity_mapping = body
    db.session.commit()

    logger.info("Entity mapping saved: request_id=%s", request_id)

    return jsonify({
        "id": str(onboarding_req.id),
        "entity_mapping": onboarding_req.entity_mapping,
    }), 200


@solutioning_bp.route("/<uuid:request_id>/workbook", methods=["PUT"])
def save_workbook(request_id):
    """Save or update workbook data for an onboarding request.

    Expects a JSON body representing the workbook.  The entire body is
    persisted to ``workbook_data``.
    """
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    onboarding_req.workbook_data = body
    db.session.commit()

    logger.info("Workbook data saved: request_id=%s", request_id)

    return jsonify({
        "id": str(onboarding_req.id),
        "workbook_data": onboarding_req.workbook_data,
    }), 200
