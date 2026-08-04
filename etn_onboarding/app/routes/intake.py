import logging

from flask import Blueprint, request, jsonify

from app.extensions import db
from app.models import OnboardingRequest, RequestStatus, AuditLog
from app.services.state_machine import transition_request, InvalidTransitionError

logger = logging.getLogger(__name__)

intake_bp = Blueprint("intake", __name__, url_prefix="/api/intake")

REQUIRED_FIELDS = {"app_name", "apm_id", "requestor_name", "requestor_email", "team", "environment"}
VALID_ENVIRONMENTS = {"dev", "stage", "prod"}


@intake_bp.route("/", methods=["POST"])
def submit_request():
    """Submit a new onboarding request.

    Expects a JSON body with at minimum: app_name, apm_id, requestor_name,
    requestor_email, team, environment.  Additional fields are stored in
    ``form_data``.
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON"}), 400

    missing = REQUIRED_FIELDS - set(body.keys())
    if missing:
        return jsonify({"error": f"Missing required fields: {sorted(missing)}"}), 400

    environment = body["environment"]
    if environment not in VALID_ENVIRONMENTS:
        return jsonify({"error": f"Invalid environment '{environment}'. Must be one of {sorted(VALID_ENVIRONMENTS)}"}), 400

    # Check for duplicate APM ID
    existing = OnboardingRequest.query.filter_by(apm_id=body["apm_id"]).first()
    if existing:
        return jsonify({"error": f"An onboarding request with apm_id '{body['apm_id']}' already exists"}), 409

    # Separate core columns from extra form data
    core_keys = {"app_name", "apm_id", "requestor_name", "requestor_email", "team", "environment"}
    form_data = {k: v for k, v in body.items() if k not in core_keys}

    onboarding_req = OnboardingRequest(
        app_name=body["app_name"],
        apm_id=body["apm_id"],
        requestor_name=body["requestor_name"],
        requestor_email=body["requestor_email"],
        team=body["team"],
        environment=environment,
        status=RequestStatus.intake_pending,
        form_data=form_data,
    )

    audit_entry = AuditLog(
        request_id=onboarding_req.id,
        stage=RequestStatus.intake_pending,
        action="submit_onboarding_request",
        actor=body["requestor_email"],
        outcome="success",
        metadata_={"source": "intake_form"},
    )

    db.session.add(onboarding_req)
    db.session.add(audit_entry)
    db.session.commit()

    logger.info("Onboarding request created: apm_id=%s id=%s", body["apm_id"], onboarding_req.id)

    return jsonify({
        "id": str(onboarding_req.id),
        "apm_id": onboarding_req.apm_id,
        "status": onboarding_req.status.value,
        "created_at": onboarding_req.created_at.isoformat(),
    }), 201


@intake_bp.route("/", methods=["GET"])
def list_requests():
    """List and filter the intake queue.

    Query parameters:
      - status: filter by request status
      - team: filter by team name
      - environment: filter by environment (dev/stage/prod)
      - page: page number (default 1)
      - per_page: items per page (default 20, max 100)
    """
    query = OnboardingRequest.query

    status_filter = request.args.get("status")
    if status_filter:
        try:
            status_enum = RequestStatus(status_filter)
        except ValueError:
            return jsonify({"error": f"Invalid status '{status_filter}'"}), 400
        query = query.filter(OnboardingRequest.status == status_enum)

    team_filter = request.args.get("team")
    if team_filter:
        query = query.filter(OnboardingRequest.team == team_filter)

    env_filter = request.args.get("environment")
    if env_filter:
        if env_filter not in VALID_ENVIRONMENTS:
            return jsonify({"error": f"Invalid environment '{env_filter}'"}), 400
        query = query.filter(OnboardingRequest.environment == env_filter)

    query = query.order_by(OnboardingRequest.created_at.desc())

    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    items = [
        {
            "id": str(r.id),
            "app_name": r.app_name,
            "apm_id": r.apm_id,
            "requestor_name": r.requestor_name,
            "requestor_email": r.requestor_email,
            "team": r.team,
            "environment": r.environment,
            "status": r.status.value,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in pagination.items
    ]

    return jsonify({
        "items": items,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total,
        "pages": pagination.pages,
    }), 200


@intake_bp.route("/<uuid:request_id>/validate", methods=["POST"])
def validate_intake(request_id):
    """Validate intake form fields and transition the request to intake_validated.

    Accepts an optional JSON body with ``actor`` (defaults to ``"system"``).
    """
    onboarding_req = db.session.get(OnboardingRequest, request_id)
    if not onboarding_req:
        return jsonify({"error": "Request not found"}), 404

    body = request.get_json(silent=True) or {}
    actor = body.get("actor", "system")

    # Validate that core fields are non-empty
    validation_errors = []
    for field in REQUIRED_FIELDS:
        value = getattr(onboarding_req, field, None)
        if not value:
            validation_errors.append(f"Field '{field}' is empty or missing")

    if validation_errors:
        return jsonify({"error": "Validation failed", "details": validation_errors}), 422

    try:
        audit_entry = transition_request(
            onboarding_req,
            RequestStatus.intake_validated,
            actor=actor,
            action="validate_intake",
            metadata={"validated_fields": sorted(REQUIRED_FIELDS)},
        )
        db.session.commit()
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409

    logger.info("Intake validated: request_id=%s actor=%s", request_id, actor)

    return jsonify({
        "id": str(onboarding_req.id),
        "status": onboarding_req.status.value,
        "audit_id": str(audit_entry.id),
    }), 200
