"""How the billing source routes answer: the problem response of each service error, and the
throttle on the admin key checks that adding or rotating a source runs with the provider."""

import uuid

from fastapi import Request

from app.config import Settings
from app.core.crypto import CryptoNotConfigured
from app.core.errors import FieldError, ProblemError, conflict, not_configured, not_found
from app.core.ratelimit import BILLING_KEY_CHECK_LIMITER, enforce_rate_limit
from app.reconciliation.errors import (
    BillingSourceNotFound,
    InvalidSourceField,
    NameTaken,
    NotApiKind,
    ProjectNotInOrg,
    ProjectTaken,
)

NEEDS_KEYS = (
    "Billing sources that fetch from a provider are encrypted with a server key that isn't "
    "set. Ask your administrator to set CREDENTIALS_KEYS, or add a CSV source."
)
SOURCE_ERRORS = (
    CryptoNotConfigured,
    BillingSourceNotFound,
    NameTaken,
    ProjectTaken,
    ProjectNotInOrg,
    NotApiKind,
    InvalidSourceField,
)


def invalid(field: str, message: str) -> ProblemError:
    return ProblemError(
        422,
        "VALIDATION_ERROR",
        "The request is invalid.",
        errors=[FieldError(field=field, message=message)],
    )


def source_problem(error: Exception) -> ProblemError:
    """The problem response for an error the billing source service raises."""
    if isinstance(error, CryptoNotConfigured):
        return not_configured(NEEDS_KEYS)
    if isinstance(error, BillingSourceNotFound):
        return not_found()
    if isinstance(error, NameTaken):
        return conflict(
            "BILLING_SOURCE_NAME_TAKEN",
            "A billing source with this name already exists in this organization.",
        )
    if isinstance(error, ProjectTaken):
        message = f"A project is already compared with {error.other_source_name}."
        return ProblemError(
            409,
            "BILLING_SOURCE_PROJECT_TAKEN",
            f"{message} A project is compared with one account per provider; "
            f"remove it from {error.other_source_name} first.",
            errors=[FieldError(field="project_ids", message=message)],
        )
    if isinstance(error, ProjectNotInOrg):
        return invalid("project_ids", "must be projects of this organization")
    if isinstance(error, NotApiKind):
        return invalid("kind", "a CSV source holds no key to rotate")
    if isinstance(error, InvalidSourceField):
        return invalid(error.field, error.message)
    raise error


async def throttle_key_check(request: Request, org_id: uuid.UUID, settings: Settings) -> None:
    """Take a token from the organization's key-check bucket, or raise 429 `RATE_LIMITED`.
    Nothing is taken while provider calls are off: no key is checked then."""
    if not settings.billing_sources_enabled:
        return
    await enforce_rate_limit(
        request,
        BILLING_KEY_CHECK_LIMITER,
        f"billing_key_check:{org_id}",
        scope="billing_key_check",
        detail="Admin keys were checked too often in this organization. Try again after "
        "Retry-After.",
    )
