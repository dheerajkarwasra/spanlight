"""The gateway's exact-match response cache, kept in Postgres.

`GatewayCache` is the interface the gateway codes against; `PostgresGatewayCache` is the
implementation. Every method is scoped by `project_id`, and the project must be bound on `db`
(`bind_project`), which row-level security needs. The methods do not commit: the caller owns
the transaction.

Only a complete, successful answer is stored: status 200 and a body of at most 1 MB. A lookup
never returns a row past its `expires_at`, whether or not the prune job has deleted it yet.
"""

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GatewayCacheEntry
from app.gateway.sse import Usage

# What the response header `X-Spanlight-Cache` says: served from the cache (`hit`), eligible but
# not found (`miss`), the key has no TTL (`off`), or the request cannot be cached, such as a
# stream (`bypass`).
CacheStatus = Literal["hit", "miss", "off", "bypass"]

MAX_BODY_BYTES = 1_048_576  # 1 MB; matches the table's CHECK


@dataclass(frozen=True, slots=True)
class CachedResponse:
    """A stored answer: the provider's bytes and content type, the model and the original usage."""

    body: bytes
    content_type: str
    model: str
    usage: Usage


class GatewayCache(Protocol):
    """Where the gateway keeps responses. A Redis implementation can sit behind the same shape."""

    async def lookup(
        self, db: AsyncSession, project_id: uuid.UUID, key: bytes, now: datetime
    ) -> CachedResponse | None:
        """The live entry for `key`, counting the hit; None if there is none or it expired."""
        ...

    async def store(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        key: bytes,
        *,
        status_code: int,
        model: str,
        body: bytes,
        content_type: str,
        usage: Usage,
        ttl_seconds: int,
        now: datetime,
    ) -> bool:
        """Keep the response for `ttl_seconds`, replacing an entry under the same key.

        Returns whether it was stored: only a 200 with a body of at most 1 MB is.
        """
        ...

    async def purge(self, db: AsyncSession, project_id: uuid.UUID) -> int:
        """Delete every entry of the project, expired or not. Returns how many."""
        ...


def usage_to_json(usage: Usage) -> dict[str, Any]:
    """The stored form of a `Usage`: the same field names."""
    return asdict(usage)


def usage_from_json(raw: dict[str, Any]) -> Usage:
    """A `Usage` from its stored form; a part absent from an older entry is unknown, not 0."""
    return Usage(
        input_tokens=int(raw["input_tokens"]),
        output_tokens=int(raw["output_tokens"]),
        cached_tokens=_optional_int(raw, "cached_tokens"),
        cache_write_tokens=_optional_int(raw, "cache_write_tokens"),
        cache_write_1h_tokens=_optional_int(raw, "cache_write_1h_tokens"),
    )


def _optional_int(raw: dict[str, Any], key: str) -> int | None:
    value = raw.get(key)
    return None if value is None else int(value)


class PostgresGatewayCache:
    """`GatewayCache` on the `gateway_cache` table (see ADR 0010)."""

    async def lookup(
        self, db: AsyncSession, project_id: uuid.UUID, key: bytes, now: datetime
    ) -> CachedResponse | None:
        # One statement finds the live row and counts the hit, so the count cannot miss a hit
        # that races another.
        row = (
            await db.execute(
                update(GatewayCacheEntry)
                .where(
                    GatewayCacheEntry.project_id == project_id,
                    GatewayCacheEntry.cache_key == key,
                    GatewayCacheEntry.expires_at > now,
                )
                .values(hit_count=GatewayCacheEntry.hit_count + 1)
                .returning(
                    GatewayCacheEntry.body,
                    GatewayCacheEntry.content_type,
                    GatewayCacheEntry.model,
                    GatewayCacheEntry.usage,
                )
                .execution_options(synchronize_session=False)
            )
        ).one_or_none()
        if row is None:
            return None
        return CachedResponse(
            body=bytes(row.body),
            content_type=row.content_type,
            model=row.model,
            usage=usage_from_json(row.usage),
        )

    async def store(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        key: bytes,
        *,
        status_code: int,
        model: str,
        body: bytes,
        content_type: str,
        usage: Usage,
        ttl_seconds: int,
        now: datetime,
    ) -> bool:
        if status_code != 200 or len(body) > MAX_BODY_BYTES or ttl_seconds < 1:
            return False
        # `created_at` is the caller's clock, like `expires_at`, so the table's CHECK
        # (`expires_at > created_at`) cannot fail on a skew between this host and the database.
        values = {
            "model": model,
            "body": body,
            "content_type": content_type,
            "usage": usage_to_json(usage),
            "created_at": now,
            "expires_at": now + timedelta(seconds=ttl_seconds),
            "hit_count": 0,
        }
        statement = insert(GatewayCacheEntry).values(project_id=project_id, cache_key=key, **values)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[GatewayCacheEntry.project_id, GatewayCacheEntry.cache_key],
                set_=values,
            )
        )
        return True

    async def purge(self, db: AsyncSession, project_id: uuid.UUID) -> int:
        result = await db.execute(
            delete(GatewayCacheEntry)
            .where(GatewayCacheEntry.project_id == project_id)
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0))
