from datetime import datetime, timezone

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.onboarding_request import OnboardingRequest, RequestStatus


ALLOWED_TRANSITIONS: dict[RequestStatus, list[RequestStatus]] = {
    RequestStatus.intake_pending: [
        RequestStatus.intake_validated,
        RequestStatus.cancelled,
    ],
    RequestStatus.intake_validated: [
        RequestStatus.engagement,
        RequestStatus.cancelled,
    ],
    RequestStatus.engagement: [
        RequestStatus.solutioning,
        RequestStatus.cancelled,
    ],
    RequestStatus.solutioning: [
        RequestStatus.delivery_collection,
        RequestStatus.cancelled,
    ],
    RequestStatus.delivery_collection: [
        RequestStatus.delivery_routing,
        RequestStatus.cancelled,
    ],
    RequestStatus.delivery_routing: [
        RequestStatus.delivery_storage,
        RequestStatus.cancelled,
    ],
    RequestStatus.delivery_storage: [
        RequestStatus.delivery_complete,
        RequestStatus.cancelled,
    ],
    RequestStatus.delivery_complete: [
        RequestStatus.validation,
        RequestStatus.cancelled,
    ],
    RequestStatus.validation: [
        RequestStatus.complete,
        RequestStatus.cancelled,
    ],
    RequestStatus.complete: [
        RequestStatus.cancelled,
    ],
    RequestStatus.cancelled: [],
}


class InvalidTransitionError(Exception):
    """Raised when a status transition is not allowed."""

    def __init__(
        self,
        current: RequestStatus,
        target: RequestStatus,
    ) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Transition from '{current.value}' to '{target.value}' is not allowed"
        )


def transition_request(
    request: OnboardingRequest,
    new_status: RequestStatus,
    actor: str,
    *,
    action: str | None = None,
    metadata: dict | None = None,
) -> AuditLog:
    """Validate and execute a status transition on an onboarding request.

    Args:
        request: The onboarding request to transition.
        new_status: The target status.
        actor: Identifier of the user or system performing the transition.
        action: Optional description of the action (defaults to
            ``"transition <old> -> <new>"``).
        metadata: Optional JSON-serialisable dict stored on the audit log.

    Returns:
        The created :class:`AuditLog` entry.

    Raises:
        InvalidTransitionError: If the transition is not permitted.
    """
    current_status = request.status
    allowed = ALLOWED_TRANSITIONS.get(current_status, [])

    if new_status not in allowed:
        raise InvalidTransitionError(current_status, new_status)

    if action is None:
        action = f"transition {current_status.value} -> {new_status.value}"

    request.status = new_status
    request.updated_at = datetime.now(timezone.utc)

    audit_entry = AuditLog(
        request_id=request.id,
        stage=new_status,
        action=action,
        actor=actor,
        outcome="success",
        metadata_=metadata or {},
    )

    db.session.add(audit_entry)

    return audit_entry
