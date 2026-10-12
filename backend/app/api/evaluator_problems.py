"""The problem responses of the evaluator service's errors, and the actor it audits."""

from fastapi import Request

from app.api.deps import Access, client_ip
from app.core.errors import FieldError, ProblemError, conflict, not_found
from app.evals.errors import (
    EvaluatorConfigError,
    EvaluatorNameTakenError,
    EvaluatorNotFoundError,
    EvaluatorReferenceNotFoundError,
)
from app.evals.service import Actor

EVALUATOR_ERRORS = (
    EvaluatorNotFoundError,
    EvaluatorReferenceNotFoundError,
    EvaluatorNameTakenError,
    EvaluatorConfigError,
)


def evaluator_problem(error: Exception) -> ProblemError:
    """The problem response for an error the evaluator service raises."""
    if isinstance(error, EvaluatorNotFoundError | EvaluatorReferenceNotFoundError):
        return not_found()
    if isinstance(error, EvaluatorNameTakenError):
        return conflict("NAME_TAKEN", "An evaluator with this name already exists here.")
    if isinstance(error, EvaluatorConfigError):
        return ProblemError(
            422,
            error.code,
            error.detail,
            errors=[FieldError(field=error.field, message=error.detail)],
        )
    raise error


def actor_of(access: Access, request: Request) -> Actor:
    return Actor(org_id=access.org.id, user_id=access.user_id, ip=client_ip(request))
