"""Audit streaming: push an organization's audit events, in chain order, to one HTTPS endpoint.

Each POST carries `{"events": [...]}`, every event the object `GET /orgs/{id}/audit` returns, in
`seq` order, signed like alert webhooks (`X-Spanlight-Signature`, `app.notifications.signing`).
`X-Spanlight-Delivery` is `<org id>:<first seq>-<last seq>`, the same for every retry of a batch,
so a receiver can drop a batch it already has. Delivery is at least once: `delivered_seq` moves
only after a 2xx, and `seq` follows commit order (migration 1014), so no event is skipped.

URLs follow the webhook rules (`app.alerts.channels.urls`): https and public addresses unless
`WEBHOOK_ALLOW_PRIVATE_TARGETS`, checked again on every connection. The signing secret is sealed
with CREDENTIALS_KEYS and shown once, when it is generated.
"""

import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.alerts.channels.http import build_alert_client, post_checked
from app.alerts.channels.secrets import generate_webhook_secret
from app.alerts.channels.urls import check_webhook_url
from app.config import Settings
from app.core.crypto import CryptoError, Sealed, decrypt, encrypt
from app.core.egress import Resolver
from app.db.models import AuditAction, AuditEvent, AuditStream, Organization, User
from app.exports.audit import audit_event_out
from app.jobs.context import TaskContext
from app.notifications.registry import PermanentDeliveryError, RetryableDeliveryError
from app.notifications.signing import sign
from app.services.audit import record_audit

logger = structlog.get_logger(__name__)

BATCH_SIZE = 500
# Batches one stream may send per run (a minute apart): 10,000 events, then the next run goes on.
MAX_BATCHES_PER_RUN = 20
# After a failure the stream waits 1, 2, 4 ... minutes before it tries again, at most an hour.
MAX_BACKOFF = timedelta(hours=1)
EVENTS_EVENT = "audit.events"
SECRET_UNREADABLE = "The signing secret cannot be decrypted with CREDENTIALS_KEYS."  # noqa: S105
TEST_EVENT = "audit.test"


class StreamNotFoundError(Exception):
    """The organization has no audit stream."""


def _seal(stream: AuditStream, secret: str, settings: Settings) -> None:
    """Raises `CryptoNotConfigured` without CREDENTIALS_KEYS."""
    sealed = encrypt(secret.encode(), settings=settings)
    stream.secret_enc = sealed.ciphertext
    stream.secret_key_id = sealed.key_id


def _open(stream: AuditStream, settings: Settings) -> str:
    sealed = Sealed(ciphertext=stream.secret_enc, key_id=stream.secret_key_id)
    return decrypt(sealed, settings=settings).decode()


def _host(url: str) -> str:
    # The audit trail records where events go, not the URL: a query string can hold a token.
    return httpx.URL(url).host


def stream_client(settings: Settings, resolver: Resolver | None = None) -> httpx.AsyncClient:
    """A delivery client under the webhook egress rules; the caller closes it."""
    return build_alert_client(
        allow_private=settings.webhook_allow_private_targets, resolver=resolver
    )


async def get_stream(db: AsyncSession, org_id: uuid.UUID) -> AuditStream | None:
    return await db.get(AuditStream, org_id)


async def head_seq(db: AsyncSession, org_id: uuid.UUID) -> int:
    """The org's newest `seq`, 0 for an empty log."""
    value = await db.scalar(select(func.max(AuditEvent.seq)).where(AuditEvent.org_id == org_id))
    return int(value or 0)


async def save_stream(
    db: AsyncSession,
    org: Organization,
    actor_id: uuid.UUID,
    *,
    url: str,
    enabled: bool,
    settings: Settings,
    now: datetime,
    resolver: Resolver | None = None,
) -> tuple[AuditStream, str | None]:
    """Create the org's stream or change its URL and switch; the caller commits.

    A new stream starts after the newest event (it streams what happens from now on) and returns
    its signing secret, the one time it is shown. A new URL clears the failure state. Raises
    `UnsafeUrlError` and `CryptoNotConfigured`. The row is locked, so two saves run one at a time.
    """
    await check_webhook_url(
        url, allow_private=settings.webhook_allow_private_targets, resolver=resolver
    )
    stream = await db.get(AuditStream, org.id, with_for_update=True)
    if stream is None:
        secret = generate_webhook_secret()
        stream = AuditStream(
            org_id=org.id,
            url=url,
            enabled=enabled,
            delivered_seq=await head_seq(db, org.id),
            created_by=actor_id,
            consecutive_failures=0,
        )
        _seal(stream, secret, settings)
        db.add(stream)
        await db.flush()
        await record_audit(
            db,
            org_id=org.id,
            actor_user_id=actor_id,
            action=AuditAction.AUDIT_STREAM_CREATE,
            target_type="audit_stream",
            target_id=org.id,
            metadata={"host": _host(url), "enabled": enabled},
        )
        return stream, secret

    changes: dict[str, Any] = {}
    if url != stream.url:
        changes["host"] = {"from": _host(stream.url), "to": _host(url)}
        stream.url = url
        stream.consecutive_failures = 0
        stream.last_error = None
        stream.last_error_at = None
    if enabled != stream.enabled:
        changes["enabled"] = {"from": stream.enabled, "to": enabled}
        stream.enabled = enabled
    if changes:
        stream.updated_at = now
        await record_audit(
            db,
            org_id=org.id,
            actor_user_id=actor_id,
            action=AuditAction.AUDIT_STREAM_UPDATE,
            target_type="audit_stream",
            target_id=org.id,
            metadata=changes,
        )
    return stream, None


async def rotate_secret(
    db: AsyncSession, org_id: uuid.UUID, actor_id: uuid.UUID, *, settings: Settings, now: datetime
) -> tuple[AuditStream, str]:
    """Replace the signing secret and return the new one; the caller commits."""
    stream = await db.get(AuditStream, org_id, with_for_update=True)
    if stream is None:
        raise StreamNotFoundError
    secret = generate_webhook_secret()
    _seal(stream, secret, settings)
    stream.updated_at = now
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.AUDIT_STREAM_ROTATE_SECRET,
        target_type="audit_stream",
        target_id=org_id,
    )
    return stream, secret


async def delete_stream(db: AsyncSession, org_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    """Stop streaming and forget the endpoint; the caller commits."""
    stream = await db.get(AuditStream, org_id, with_for_update=True)
    if stream is None:
        raise StreamNotFoundError
    host = _host(stream.url)
    await db.delete(stream)
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.AUDIT_STREAM_DELETE,
        target_type="audit_stream",
        target_id=org_id,
        metadata={"host": host},
    )


async def _post(
    client: httpx.AsyncClient,
    stream: AuditStream,
    secret: str,
    *,
    body: bytes,
    event: str,
    delivery_id: str,
) -> None:
    timestamp = int(time.time())
    await post_checked(
        client,
        stream.url,
        body=body,
        headers={
            "x-spanlight-event": event,
            "x-spanlight-delivery": delivery_id,
            "x-spanlight-timestamp": str(timestamp),
            "x-spanlight-signature": sign(secret.encode(), timestamp, body),
        },
    )


async def send_test(
    db: AsyncSession,
    org: Organization,
    actor_id: uuid.UUID,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    now: datetime,
) -> str | None:
    """POST one `audit.test` event to the stream now. Returns None on a 2xx, else the reason.

    Audited either way; the caller commits. The message never contains the URL or the secret.
    """
    stream = await db.get(AuditStream, org.id)
    if stream is None:
        raise StreamNotFoundError
    body = json.dumps(
        {
            "event": TEST_EVENT,
            "org": {"id": str(org.id), "name": org.name},
            "sent_at": now.isoformat(),
        },
        separators=(",", ":"),
    ).encode()
    error: str | None = None
    try:
        await _post(
            client,
            stream,
            _open(stream, settings),
            body=body,
            event=TEST_EVENT,
            delivery_id=f"{org.id}:test-{uuid.uuid4().hex}",
        )
    except (PermanentDeliveryError, RetryableDeliveryError) as failure:
        error = str(failure)
    await record_audit(
        db,
        org_id=org.id,
        actor_user_id=actor_id,
        action=AuditAction.AUDIT_STREAM_TEST,
        target_type="audit_stream",
        target_id=org.id,
        outcome="success" if error is None else "failure",
        reason=None if error is None else "delivery_failed",
        metadata={"host": _host(stream.url)},
    )
    return error


def _backing_off(stream: AuditStream, now: datetime) -> bool:
    if stream.consecutive_failures == 0 or stream.last_error_at is None:
        return False
    wait = min(timedelta(minutes=2 ** (stream.consecutive_failures - 1)), MAX_BACKOFF)
    return now < stream.last_error_at + wait


async def _deliver_batch(
    session_factory: async_sessionmaker[AsyncSession],
    org_id: uuid.UUID,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    now: datetime,
) -> int | None:
    """Send the next batch of one stream. Returns how many events went, 0 when nothing is due,
    None when the stream should not continue this run (off, backing off, busy or failed).

    One transaction per batch, holding the stream row while it posts, so two workers never send
    the same batch at once.
    """
    actor = aliased(User)
    async with session_factory() as db:
        stream = (
            await db.execute(
                select(AuditStream)
                .where(AuditStream.org_id == org_id)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if stream is None or not stream.enabled or _backing_off(stream, now):
            return None
        rows = (
            await db.execute(
                select(AuditEvent, actor)
                .outerjoin(actor, actor.id == AuditEvent.actor_user_id)
                .where(AuditEvent.org_id == org_id, AuditEvent.seq > stream.delivered_seq)
                .order_by(AuditEvent.seq)
                .limit(BATCH_SIZE)
            )
        ).all()
        if not rows:
            return 0
        events = [audit_event_out(event, user).model_dump(mode="json") for event, user in rows]
        first, last = rows[0][0].seq, rows[-1][0].seq
        body = json.dumps({"events": events}, separators=(",", ":"), ensure_ascii=False).encode()
        try:
            secret = _open(stream, settings)
        except CryptoError:
            secret = None
        try:
            if secret is None:
                raise PermanentDeliveryError(SECRET_UNREADABLE)
            await _post(
                client,
                stream,
                secret,
                body=body,
                event=EVENTS_EVENT,
                delivery_id=f"{org_id}:{first}-{last}",
            )
        except (PermanentDeliveryError, RetryableDeliveryError) as failure:
            stream.consecutive_failures += 1
            stream.last_error = str(failure)
            stream.last_error_at = now
            await db.commit()
            logger.warning(
                "audit_stream_failed",
                org_id=str(org_id),
                failures=stream.consecutive_failures,
                error=str(failure),
            )
            return None
        stream.delivered_seq = int(last or stream.delivered_seq)
        stream.last_delivered_at = now
        stream.consecutive_failures = 0
        stream.last_error = None
        stream.last_error_at = None
        await db.commit()
        return len(rows)


async def deliver_streams(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: datetime,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Send what every enabled stream has pending. Returns how many events went."""
    async with session_factory() as db:
        org_ids = (
            await db.scalars(select(AuditStream.org_id).where(AuditStream.enabled.is_(True)))
        ).all()
    if not org_ids:
        return 0
    own_client = client is None
    http = client or stream_client(settings)
    total = 0
    try:
        for org_id in org_ids:
            for _ in range(MAX_BATCHES_PER_RUN):
                sent = await _deliver_batch(
                    session_factory, org_id, settings=settings, client=http, now=now
                )
                if not sent:
                    break
                total += sent
    finally:
        if own_client:
            await http.aclose()
    return total


async def run_deliver_audit_streams(context: TaskContext, _: dict[str, Any]) -> None:
    sent = await deliver_streams(context.session_factory, context.settings, now=context.clock())
    if sent:
        logger.info("audit_streams_delivered", events=sent)
