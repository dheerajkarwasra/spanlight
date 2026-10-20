"""The audit log's stream to an HTTPS endpoint, and the check of its hash chain.

Reading needs `audit:read` (admins and owners). Changing where an organization's audit trail goes
needs `org:security` (owners): the stream sends every event, sign-ins included, off the instance.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.alerts.channel_test import CHANNEL_TEST_LIMITER
from app.alerts.channels.urls import UnsafeUrlError
from app.api.deps import Access, ClockDep, DbSession, SettingsDep, require
from app.api.schemas import (
    AuditIntegrityOut,
    AuditStreamIn,
    AuditStreamOut,
    AuditStreamSavedOut,
    AuditStreamTestOut,
)
from app.core.crypto import CryptoNotConfigured
from app.core.egress import Resolver
from app.core.errors import FieldError, ProblemError, conflict, not_configured
from app.core.permissions import Permission
from app.core.ratelimit import enforce_rate_limit
from app.core.security import mark_uncacheable
from app.db.models import AuditStream
from app.services import audit_stream
from app.services.audit import check_audit_chain

router = APIRouter(tags=["orgs"])

AuditReader = Annotated[Access, Depends(require(Permission.AUDIT_READ))]
StreamOwner = Annotated[Access, Depends(require(Permission.ORG_SECURITY))]


def _not_set_up() -> ProblemError:
    # 409, not 404: the organization exists and the caller may see it; it just has no stream yet.
    return conflict("AUDIT_STREAM_NOT_SET_UP", "This organization has no audit stream.")


NEEDS_KEYS = (
    "Audit streaming signs every request with a secret kept encrypted by a server key that "
    "isn't set. Ask your administrator to set CREDENTIALS_KEYS."
)


def _resolver(request: Request) -> Resolver | None:
    # The same test seam the alert channels use for the DNS that URLs are vetted against.
    resolver: Resolver | None = getattr(request.app.state, "egress_resolver", None)
    return resolver


async def _out(db: DbSession, stream: AuditStream) -> AuditStreamOut:
    return AuditStreamOut(
        url=stream.url,
        enabled=stream.enabled,
        delivered_seq=stream.delivered_seq,
        head_seq=await audit_stream.head_seq(db, stream.org_id),
        last_delivered_at=stream.last_delivered_at,
        last_error=stream.last_error,
        last_error_at=stream.last_error_at,
        consecutive_failures=stream.consecutive_failures,
        created_at=stream.created_at,
        updated_at=stream.updated_at,
    )


@router.get("/orgs/{org_id}/audit/integrity", response_model=AuditIntegrityOut)
async def audit_integrity(
    org_id: uuid.UUID, access: AuditReader, db: DbSession
) -> AuditIntegrityOut:
    """Recompute the org's audit hash chain from the stored events and say where it breaks."""
    check = await check_audit_chain(db, access.org.id)
    return AuditIntegrityOut(
        ok=check.ok,
        checked=check.checked,
        first_seq=check.first_seq,
        head_seq=check.head_seq,
        head_hash=check.head_hash.hex() if check.head_hash else None,
        first_broken_seq=check.first_broken_seq,
    )


@router.get("/orgs/{org_id}/audit/stream", response_model=AuditStreamOut | None)
async def get_audit_stream(
    org_id: uuid.UUID, access: AuditReader, db: DbSession
) -> AuditStreamOut | None:
    """The org's audit stream, or `null` when none is set up. Never the signing secret."""
    stream = await audit_stream.get_stream(db, access.org.id)
    return await _out(db, stream) if stream else None


@router.put("/orgs/{org_id}/audit/stream", response_model=AuditStreamSavedOut)
async def put_audit_stream(
    org_id: uuid.UUID,
    body: AuditStreamIn,
    request: Request,
    response: Response,
    access: StreamOwner,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> AuditStreamSavedOut:
    """Create the stream (its `secret` is returned this once) or change its URL or switch.

    A new stream sends the events written after it is created. `422 VALIDATION_ERROR` for a URL
    the webhook rules refuse (`422 UNSAFE_URL`), `409 NOT_CONFIGURED` without CREDENTIALS_KEYS.
    """
    try:
        stream, secret = await audit_stream.save_stream(
            db,
            access.org,
            access.user_id,
            url=body.url,
            enabled=body.enabled,
            settings=settings,
            now=clock(),
            resolver=_resolver(request),
        )
    except UnsafeUrlError as error:
        raise ProblemError(
            422,
            "UNSAFE_URL",
            "The stream URL is not allowed.",
            errors=[FieldError(field="url", message=str(error))],
        ) from None
    except CryptoNotConfigured:
        raise not_configured(NEEDS_KEYS) from None
    await db.commit()
    mark_uncacheable(response)
    return AuditStreamSavedOut(stream=await _out(db, stream), secret=secret)


@router.post("/orgs/{org_id}/audit/stream/rotate-secret", response_model=AuditStreamSavedOut)
async def rotate_audit_stream_secret(
    org_id: uuid.UUID,
    response: Response,
    access: StreamOwner,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> AuditStreamSavedOut:
    """Replace the signing secret; the new one is returned this once. The old one stops at once."""
    try:
        stream, secret = await audit_stream.rotate_secret(
            db, access.org.id, access.user_id, settings=settings, now=clock()
        )
    except audit_stream.StreamNotFoundError:
        raise _not_set_up() from None
    except CryptoNotConfigured:
        raise not_configured(NEEDS_KEYS) from None
    await db.commit()
    mark_uncacheable(response)
    return AuditStreamSavedOut(stream=await _out(db, stream), secret=secret)


@router.delete("/orgs/{org_id}/audit/stream", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audit_stream(org_id: uuid.UUID, access: StreamOwner, db: DbSession) -> Response:
    """Stop streaming and forget the endpoint and its secret."""
    try:
        await audit_stream.delete_stream(db, access.org.id, access.user_id)
    except audit_stream.StreamNotFoundError:
        raise _not_set_up() from None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/orgs/{org_id}/audit/stream/test", response_model=AuditStreamTestOut)
async def test_audit_stream(
    org_id: uuid.UUID,
    request: Request,
    access: StreamOwner,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> AuditStreamTestOut:
    """POST one signed `audit.test` event to the endpoint now and report how it answered.

    At most 10 an hour per organization (`429`), like a channel's test.
    """
    if await audit_stream.get_stream(db, access.org.id) is None:
        raise _not_set_up()
    await enforce_rate_limit(
        request,
        CHANNEL_TEST_LIMITER,
        f"audit_stream_test:{access.org.id}",
        scope="audit_stream_test",
        detail="The audit stream was tested 10 times in the last hour. Try again later.",
    )
    async with audit_stream.stream_client(settings, _resolver(request)) as client:
        try:
            error = await audit_stream.send_test(
                db, access.org, access.user_id, settings=settings, client=client, now=clock()
            )
        except CryptoNotConfigured:
            raise not_configured(NEEDS_KEYS) from None
    await db.commit()
    return AuditStreamTestOut(ok=error is None, error=error)
