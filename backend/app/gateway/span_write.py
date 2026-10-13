"""One try at writing a gateway call's span and what the call used, in one transaction.

`write_span` writes the span through `ingest_spans` (with the key as `source_key_id`), counts the
call and its tokens in the key's current minute (`record_minute`; a cache hit counts zero
tokens), and moves the `last_used_at` of the key and of each provider credential the call used,
each at most once a minute. A call without a key (an in-process caller) writes only the span and
the credentials' `last_used_at`. It raises what the database raises; `app.gateway.recorder`
decides about retries.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GatewayKey, ProviderCredential, Span
from app.db.rls import bind_project
from app.gateway.context import GatewayContext
from app.gateway.key_limits import record_minute
from app.ingest.pipeline import IngestOutcome, IngestTarget, ingest_spans

LAST_USED_INTERVAL = timedelta(minutes=1)


async def write_span(
    context: GatewayContext,
    target: IngestTarget,
    span: dict[str, Any],
    credential_ids: Sequence[uuid.UUID],
    now: datetime,
    *,
    retry: bool = False,
) -> IngestOutcome:
    """One try, in one transaction: the span, the key's minute and the `last_used_at` moves.

    A `retry` first looks for the span: an earlier try may have committed and still raised (its
    acknowledgement lost), and then nothing more is written, so the minute counts the call once.
    """
    async with context.sessions() as db, db.begin():
        if retry and await _already_stored(db, context.project_id, span):
            return IngestOutcome(accepted=1)
        # `ingest_spans` binds the project, which the key's rows below need as well.
        outcome = await ingest_spans(db, target, [span], now=now)
        if context.key is not None:
            await record_minute(
                db, context.key.key.id, context.project_id, _total_tokens(span), now
            )
            await _touch_key(db, context.key.key, now)
        await _touch_credentials(db, context, credential_ids, now)
    return outcome


async def _already_stored(db: AsyncSession, project_id: uuid.UUID, span: dict[str, Any]) -> bool:
    await bind_project(db, project_id)
    found = await db.scalar(
        select(Span.span_id).where(
            Span.project_id == project_id,
            Span.trace_id == span.get("trace_id"),
            Span.span_id == span.get("span_id"),
        )
    )
    return found is not None


async def _touch_key(db: AsyncSession, key: GatewayKey, now: datetime) -> None:
    """Move `last_used_at` to now unless it moved less than a minute ago."""
    if key.last_used_at is not None and now - key.last_used_at < LAST_USED_INTERVAL:
        return
    await db.execute(
        update(GatewayKey)
        .where(
            GatewayKey.id == key.id,
            or_(
                GatewayKey.last_used_at.is_(None),
                GatewayKey.last_used_at < now - LAST_USED_INTERVAL,
            ),
        )
        .values(last_used_at=now)
        .execution_options(synchronize_session=False)
    )


async def _touch_credentials(
    db: AsyncSession,
    context: GatewayContext,
    credential_ids: Sequence[uuid.UUID],
    now: datetime,
) -> None:
    """Move each used credential's `last_used_at` to now, at most once a minute, like the key's.

    `provider_credentials` has no row-level security: the `org_id` condition scopes the update.
    """
    for credential_id in credential_ids:
        loaded = context.credentials.get(credential_id)
        seen = loaded.last_used_at if loaded is not None else None
        if seen is not None and now - seen < LAST_USED_INTERVAL:
            continue  # moved less than a minute ago, as far as this call's snapshot knows
        await db.execute(
            update(ProviderCredential)
            .where(
                ProviderCredential.org_id == context.org_id,
                ProviderCredential.id == credential_id,
                or_(
                    ProviderCredential.last_used_at.is_(None),
                    ProviderCredential.last_used_at < now - LAST_USED_INTERVAL,
                ),
            )
            .values(last_used_at=now)
            .execution_options(synchronize_session=False)
        )


def _total_tokens(span: dict[str, Any]) -> int:
    usage = span.get("usage") or {}
    return sum(
        value
        for value in (usage.get("input_tokens"), usage.get("output_tokens"))
        if isinstance(value, int)
    )


__all__ = ["LAST_USED_INTERVAL", "write_span"]
