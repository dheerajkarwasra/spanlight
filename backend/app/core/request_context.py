"""Per-request context shared by logging, errors, middleware and the audit log."""

import uuid
from contextvars import ContextVar
from dataclasses import dataclass

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str | None) -> None:
    _request_id.set(request_id)


@dataclass
class AuditContext:
    """Who is making the request and from where, for `app.services.audit.record_audit`.

    The middleware sets the client; authentication and `require()` fill in the rest. It is one
    mutable object per request, so a dependency that fills it in is seen by the handler after it.
    """

    ip: str | None = None
    user_agent: str | None = None
    user_id: uuid.UUID | None = None
    user_email: str | None = None
    actor_type: str | None = None  # an `AuditActorType` value
    credential_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None


_audit_context: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


def current_audit_context() -> AuditContext | None:
    """None outside a request: in the worker and the CLI."""
    return _audit_context.get()


def set_audit_context(context: AuditContext | None) -> None:
    _audit_context.set(context)
