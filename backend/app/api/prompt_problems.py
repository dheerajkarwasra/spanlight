"""The problem responses of the prompt service's errors, and the versions cursor."""

import uuid

import structlog

from app.api.deps import Access
from app.core.errors import FieldError, ProblemError, conflict, not_found, too_many_requests
from app.core.pagination import decode_cursor
from app.prompts import service
from app.prompts.errors import (
    KindMismatchError,
    LabelReservedError,
    NoChangesError,
    PromptInUseError,
    PromptNameTakenError,
    PromptNotFoundError,
    VersionRateLimitedError,
)
from app.prompts.templates import InvalidTemplateError

logger = structlog.get_logger(__name__)

PROMPT_ERRORS = (
    PromptNotFoundError,
    PromptNameTakenError,
    KindMismatchError,
    NoChangesError,
    PromptInUseError,
    LabelReservedError,
    VersionRateLimitedError,
    InvalidTemplateError,
)


def prompt_problem(error: Exception) -> ProblemError:
    """The problem response for an error the prompt service raises."""
    if isinstance(error, PromptNotFoundError):
        return not_found()
    if isinstance(error, PromptNameTakenError):
        return conflict("NAME_TAKEN", "A prompt with this name already exists here.")
    if isinstance(error, KindMismatchError):
        return conflict(
            "KIND_MISMATCH", "A prompt keeps its kind: text stays text, chat stays chat."
        )
    if isinstance(error, NoChangesError):
        return conflict(
            "NO_CHANGES", "The template and model config are the same as the latest version's."
        )
    if isinstance(error, PromptInUseError):
        return conflict(
            "PROMPT_IN_USE",
            "An evaluator or experiment uses a version of this prompt. Change or delete it first.",
        )
    if isinstance(error, LabelReservedError):
        return ProblemError(
            422,
            "LABEL_RESERVED",
            "The label 'latest' is reserved: it always means the highest version.",
            errors=[FieldError(field="label", message="Reserved")],
        )
    if isinstance(error, VersionRateLimitedError):
        return too_many_requests(
            error.retry_after_seconds,
            f"A prompt can get {service.MAX_VERSIONS_PER_DAY} versions a day (UTC). "
            "Try again after midnight UTC.",
        )
    if isinstance(error, InvalidTemplateError):
        return ProblemError(
            422,
            "VALIDATION_ERROR",
            "The template is not valid.",
            errors=[FieldError(field="template", message=str(error))],
        )
    raise error


def cursor_version(cursor: str | None) -> int | None:
    if not cursor:
        return None
    try:
        return int(decode_cursor(cursor)[1])
    except ValueError:
        raise ProblemError(
            422,
            "VALIDATION_ERROR",
            "The pagination cursor is invalid.",
            errors=[FieldError(field="cursor", message="Invalid cursor")],
        ) from None


def log_prompt(event: str, access: Access, prompt_id: uuid.UUID, **fields: object) -> None:
    logger.info(
        event,
        org_id=str(access.org.id),
        project_id=str(access.require_project().id),
        prompt_id=str(prompt_id),
        **fields,
    )
