"""The audit log's stream to an HTTPS endpoint, and the check of its hash chain.

Reading needs `audit:read` (admins and owners). Changing where an organization's audit trail goes
needs `org:security` (owners): the stream sends every event, sign-ins included, off the instance.
"""

import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request, Response, status

from app.alerts.channel_test import CHANNEL_TEST_LIMITER
from app.alerts.channels.urls import UnsafeUrlError, check_webhook_url
from app.api.deps import Access, ClockDep, CurrentSession, DbSession, SettingsDep, require
from app.api.schemas import (
    AuditIntegrityOut,
    AuditStreamIn,
    AuditStreamOut,
    AuditStreamSavedOut,
    AuditStreamTestOut,
)
from app.api.schemas.orgs import AuditStreamProofIn
from app.auth.reauth import confirm_account_owner
from app.core.crypto import CryptoNotConfigured
from app.core.egress import Resolver
from app.core.errors import FieldError, ProblemError, conflict, not_configured
from app.core.permissions import Permission
from app.core.ratelimit import enforce_rate_limit
from app.core.security import mark_uncacheable
from app.db.models import AuditStream
from app.services import audit_stream
from app.services.audit import check_audit_chain
from app.services.audit_stream_notices import enqueue_audit_stream_notices, saved_stream_change

router = APIRouter(tags=["orgs"])

AuditReader = Annotated[Access, Depends(require(Permission.AUDIT_READ))]
StreamOwner = Annotated[Access, Depends(require(Permission.ORG_SECURITY))]


def _unsafe_url(error: UnsafeUrlError) -> ProblemError:
    return ProblemError(
        422,
        "UNSAFE_URL",
        "The stream URL is not allowed.",
        errors=[FieldError(field="url", message=str(error))],
    )


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
    auth: CurrentSession,
    access: StreamOwner,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> AuditStreamSavedOut:
    """Create the stream (its `secret` is returned this once) or change its URL or switch.

    Every PUT requires a session and account-owner proof (`password`, plus `code` with 2FA;
    passwordless accounts need a sign-in within 10 minutes). Tokens get `403 SESSION_REQUIRED`.
    A new stream sends events written after creation. Crypto configuration (`409 NOT_CONFIGURED`)
    and webhook URL checks (`422 UNSAFE_URL`) run before proof so a typo spends no code.
    """
    if not settings.is_crypto_configured:
        raise not_configured(NEEDS_KEYS)
    try:
        await check_webhook_url(
            body.url,
            allow_private=settings.webhook_allow_private_targets,
            resolver=_resolver(request),
        )
    except UnsafeUrlError as error:
        raise _unsafe_url(error) from None
    await confirm_account_owner(
        db,
        auth,
        body.password,
        body.code,
        scope="audit_stream_reauth",
        settings=settings,
        now=clock(),
    )
    try:
        async with db.begin_nested():
            stream, secret, previous = await audit_stream.save_stream(
                db,
                access.org,
                access.user_id,
                url=body.url,
                enabled=body.enabled,
                settings=settings,
                now=clock(),
                resolver=_resolver(request),
            )
            change = saved_stream_change(stream.url, stream.enabled, previous)
            if change is not None and settings.is_email_configured:
                await enqueue_audit_stream_notices(
                    db,
                    org=access.org,
                    actor=auth.user,
                    host=httpx.URL(stream.url).host,
                    change=change,
                )
            result = AuditStreamSavedOut(stream=await _out(db, stream), secret=secret)
    except Exception as error:
        # Keep a spent second-factor code spent, even if the write is rolled back.
        await db.commit()
        if isinstance(error, UnsafeUrlError):
            raise _unsafe_url(error) from None
        if isinstance(error, CryptoNotConfigured):
            raise not_configured(NEEDS_KEYS) from None
        raise
    await db.commit()
    mark_uncacheable(response)
    return result


@router.post("/orgs/{org_id}/audit/stream/rotate-secret", response_model=AuditStreamSavedOut)
async def rotate_audit_stream_secret(
    org_id: uuid.UUID,
    body: AuditStreamProofIn,
    response: Response,
    auth: CurrentSession,
    access: StreamOwner,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> AuditStreamSavedOut:
    """Replace the signing secret; the new one is returned this once. The old one stops at once.

    Requires an owner session and a JSON proof body (`password`, plus `code` with 2FA;
    passwordless accounts need a sign-in within 10 minutes). Tokens get `403 SESSION_REQUIRED`.
    An absent stream returns `409 AUDIT_STREAM_NOT_SET_UP` before proof.
    """
    if await audit_stream.get_stream(db, access.org.id) is None:
        raise _not_set_up()
    if not settings.is_crypto_configured:
        raise not_configured(NEEDS_KEYS)
    await confirm_account_owner(
        db,
        auth,
        body.password,
        body.code,
        scope="audit_stream_reauth",
        settings=settings,
        now=clock(),
    )
    try:
        async with db.begin_nested():
            stream, secret = await audit_stream.rotate_secret(
                db, access.org.id, access.user_id, settings=settings, now=clock()
            )
            if settings.is_email_configured:
                await enqueue_audit_stream_notices(
                    db,
                    org=access.org,
                    actor=auth.user,
                    host=httpx.URL(stream.url).host,
                    change="secret rotated",
                )
            result = AuditStreamSavedOut(stream=await _out(db, stream), secret=secret)
    except Exception as error:
        await db.commit()
        if isinstance(error, audit_stream.StreamNotFoundError):
            raise _not_set_up() from None
        if isinstance(error, CryptoNotConfigured):
            raise not_configured(NEEDS_KEYS) from None
        raise
    await db.commit()
    mark_uncacheable(response)
    return result


@router.delete("/orgs/{org_id}/audit/stream", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audit_stream(
    org_id: uuid.UUID,
    body: AuditStreamProofIn,
    auth: CurrentSession,
    access: StreamOwner,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> Response:
    """Stop streaming and forget the endpoint and its secret after account-owner proof.

    Requires an owner session and a JSON proof body (`password`, plus `code` with 2FA;
    passwordless accounts need a sign-in within 10 minutes). Tokens get `403 SESSION_REQUIRED`.
    An absent stream returns `409 AUDIT_STREAM_NOT_SET_UP` before proof.
    """
    if await audit_stream.get_stream(db, access.org.id) is None:
        raise _not_set_up()
    await confirm_account_owner(
        db,
        auth,
        body.password,
        body.code,
        scope="audit_stream_reauth",
        settings=settings,
        now=clock(),
    )
    try:
        async with db.begin_nested():
            host = await audit_stream.delete_stream(db, access.org.id, access.user_id)
            if settings.is_email_configured:
                await enqueue_audit_stream_notices(
                    db,
                    org=access.org,
                    actor=auth.user,
                    host=host,
                    change="removed",
                )
    except Exception as error:
        await db.commit()
        if isinstance(error, audit_stream.StreamNotFoundError):
            raise _not_set_up() from None
        raise
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
