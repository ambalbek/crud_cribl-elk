import logging

from flask import Blueprint, request, jsonify

from app.extensions import db
from app.models import OnboardingRequest, RequestStatus, AuditLog
from app.services.state_machine import transition_request, InvalidTransitionError

logger = logging.getLogger(__name__)

engagement_bp = Blueprint("engagement", __name__, url_prefix="/api/engagement")


@engagement_bp.route("/<uuid:request_id>/schedule-meeting", methods=["POST"])
def schedule_meeting(request_id):
    """Record an engagement meeting and transition the request to engagement.

    Expects JSON body with:
      - meeting_date (str, ISO-8601)
      - attendees (list of strings)
      - actor (str, optional -- defaults to 'system')
      - notes (str, optional)
    """
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON"}), 400

    meeting_date = body.get("meeting_date")
    attendees = body.get("attendees")
    if not meeting_date:
        return jsonify({"error": "meeting_date is required"}), 400
    if not attendees or not isinstance(attendees, list):
        return jsonify({"error": "attendees must be a non-empty list"}), 400

    actor = body.get("actor", "system")

    try:
        audit_entry = transition_request(
            onboarding_req,
            RequestStatus.engagement,
            actor=actor,
            action="schedule_meeting",
            metadata={
                "meeting_date": meeting_date,
                "attendees": attendees,
                "notes": body.get("notes", ""),
            },
        )
        db.session.commit()
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409

    logger.info(
        "Engagement meeting scheduled: request_id=%s date=%s attendees=%d",
        request_id, meeting_date, len(attendees),
    )

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "audit_id": str(audit_entry.id),
        "meeting_date": meeting_date,
        "attendees": attendees,
    }), 200


@engagement_bp.route("/<uuid:request_id>/change-request", methods=["POST"])
def change_request(request_id):
    """Handle a change or review request.

    Stores notes/comments on the request without changing its status.

    Expects JSON body with:
      - notes (str, required)
      - actor (str, optional)
    """
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON"}), 400

    notes = body.get("notes")
    if not notes:
        return jsonify({"error": "notes field is required"}), 400

    actor = body.get("actor", "system")

    audit_entry = AuditLog(
        request_id=onboarding_req.id,
        stage=onboarding_req.status,
        action="change_request",
        actor=actor,
        outcome="success",
        metadata_={"notes": notes},
    )
    db.session.add(audit_entry)
    db.session.commit()

    logger.info("Change request recorded: request_id=%s actor=%s", request_id, actor)

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "audit_id": str(audit_entry.id),
    }), 200
