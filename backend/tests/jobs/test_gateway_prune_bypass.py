"""`prune_gateway_cache` deletes across all projects with the bypass. It must remove exactly the
expired cache entries and the old key minutes, from every project, and nothing else.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.gateway.jobs import MINUTES_RETENTION, prune_gateway_cache
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_row

_CACHE = "SELECT project_id, cache_key FROM gateway_cache"
_MINUTES = "SELECT project_id, key_id, minute_start FROM gateway_key_minutes"


async def _cache_entry(
    session_factory: SessionFactory, project_id: uuid.UUID, expires_at: datetime
) -> bytes:
    row = await seed_row(session_factory, "gateway_cache", project_id)
    await execute(
        session_factory,
        "UPDATE gateway_cache SET expires_at = :expires_at, "
        "created_at = :expires_at - interval '1 hour' "
        "WHERE project_id = :project_id AND cache_key = :cache_key",
        {"expires_at": expires_at, "project_id": project_id, "cache_key": row["cache_key"]},
    )
    return bytes(row["cache_key"])


async def _minute(
    session_factory: SessionFactory, project_id: uuid.UUID, minute_start: datetime
) -> tuple[Any, datetime]:
    row = await seed_row(session_factory, "gateway_key_minutes", project_id)
    await execute(
        session_factory,
        "UPDATE gateway_key_minutes SET minute_start = :new WHERE key_id = :key_id "
        "AND minute_start = :old",
        {"new": minute_start, "key_id": row["key_id"], "old": row["minute_start"]},
    )
    return row["key_id"], minute_start


async def test_prune_removes_only_expired_entries_and_old_minutes_in_every_project(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    a, b = await new_projects(session_factory, 2)
    kept_cache, kept_minutes = [], []
    for project in (a, b):
        await _cache_entry(session_factory, project, now - timedelta(minutes=1))
        kept_cache.append(await _cache_entry(session_factory, project, now + timedelta(minutes=1)))
        await _minute(session_factory, project, now - MINUTES_RETENTION - timedelta(minutes=1))
        kept_minutes.append(await _minute(session_factory, project, now - timedelta(minutes=30)))
    keys = await fetch(session_factory, "SELECT id, project_id, route_id FROM gateway_keys")
    routes = await fetch(session_factory, "SELECT id, project_id FROM gateway_routes")

    counts = await prune_gateway_cache(session_factory, now=now)

    assert counts == {"cache_entries_deleted": 2, "key_minutes_deleted": 2}
    cache = {
        (row["project_id"], bytes(row["cache_key"])) for row in await fetch(session_factory, _CACHE)
    }
    assert cache == {(a, kept_cache[0]), (b, kept_cache[1])}
    minutes = {
        (row["project_id"], row["key_id"], row["minute_start"])
        for row in await fetch(session_factory, _MINUTES)
    }
    assert {(key_id, started) for _, key_id, started in minutes} == set(kept_minutes)
    assert {project for project, _, _ in minutes} == {a, b}
    assert await fetch(session_factory, "SELECT id, project_id, route_id FROM gateway_keys") == keys
    assert await fetch(session_factory, "SELECT id, project_id FROM gateway_routes") == routes
