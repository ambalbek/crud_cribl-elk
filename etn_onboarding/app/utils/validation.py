"""Input validation helpers for ETN Onboarding forms."""
from __future__ import annotations

import re
from typing import List, Tuple

_VALID_ENVIRONMENTS = {"dev", "stage", "prod"}

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)

_REQUIRED_FIELDS = (
    "app_name",
    "apm_id",
    "requestor_name",
    "requestor_email",
    "team",
    "environment",
)


def validate_environment(env: str) -> bool:
    """Return ``True`` if *env* is a recognised environment name.

    Valid values are ``dev``, ``stage``, and ``prod`` (case-insensitive).
    """
    return env.strip().lower() in _VALID_ENVIRONMENTS


def validate_email(email: str) -> bool:
    """Return ``True`` if *email* looks like a valid email address.

    This performs a basic format check (RFC-5322-ish) — it does **not**
    verify that the mailbox exists.
    """
    return bool(_EMAIL_RE.match(email.strip()))


def validate_onboarding_form(data: dict) -> Tuple[bool, List[str]]:
    """Validate the onboarding-request form payload.

    Parameters
    ----------
    data:
        Dictionary of form fields submitted by the user.

    Returns
    -------
    tuple[bool, list[str]]
        ``(is_valid, errors)`` — *is_valid* is ``True`` when the payload
        passes all checks; *errors* contains human-readable messages for
        every failed validation.
    """
    errors: List[str] = []

    # --- required fields ---
    for field in _REQUIRED_FIELDS:
        value = data.get(field)
        if not value or (isinstance(value, str) and not value.strip()):
            errors.append(f"'{field}' is required.")

    # --- field-specific validations (only if value is present) ---
    env = data.get("environment")
    if env and not validate_environment(env):
        errors.append(
            f"Invalid environment '{env}'. Must be one of: "
            f"{', '.join(sorted(_VALID_ENVIRONMENTS))}."
        )

    email = data.get("requestor_email")
    if email and not validate_email(email):
        errors.append(f"Invalid email format: '{email}'.")

    return (len(errors) == 0, errors)
