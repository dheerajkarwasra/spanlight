"""The credential in-use scan visits every project of an organization and puts the binding back.

`credential_in_use` (`app.gateway.queries`) binds each of the organization's projects in turn so
row-level security lets it read their routes. A request that called it must still be bound to
its own project afterwards, or the rest of the request would read (or write) another tenant's
rows: whatever the scan finds, however it ends.
"""

import json
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project, bypass_rls
from app.gateway import queries

Sessions = async_sessionmaker[AsyncSession]

_CURRENT_BINDING = text("SELECT current_setting('app.project_id', true)")
_TRACE_PROJECTS = text("SELECT DISTINCT project_id FROM traces")


@dataclass(frozen=True)
class Org:
    """One organization: three projects, one credential, and a route naming it in `routed`."""

    credential_id: uuid.UUID
    projects: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
    routed: uuid.UUID  # the project whose route targets the credential


async def _insert_route(db: AsyncSession, project_id: uuid.UUID, credential_id: uuid.UUID) -> None:
    config = {"targets": [{"credential_id": str(credential_id)}]}
    await db.execute(
        text(
            "INSERT INTO gateway_routes (id, project_id, name, config) "
            "VALUES (gen_random_uuid(), :project_id, 'main', CAST(:config AS jsonb))"
        ),
        {"project_id": project_id, "config": json.dumps(config)},
    )


async def _insert_org(db: AsyncSession, *, route_in_project: int | None) -> Org:
    """An organization with three projects (ids in ascending order, the scan's order), a
    credential, and a trace in every project. `route_in_project` is the index of the project
    whose route names the credential, or None for no route."""
    org_id, credential_id = uuid.uuid4(), uuid.uuid4()
    projects = tuple(sorted(uuid.uuid4() for _ in range(3)))
    await db.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Org', :slug)"),
        {"id": org_id, "slug": f"org-{org_id.hex[:12]}"},
    )
    await db.execute(
        text(
            "INSERT INTO provider_credentials (id, org_id, provider, name, ciphertext, key_id) "
            "VALUES (:id, :org_id, 'openai', 'key', '\\x00'::bytea, 'test')"
        ),
        {"id": credential_id, "org_id": org_id},
    )
    for index, project_id in enumerate(projects):
        await db.execute(
            text("INSERT INTO projects (id, org_id, name, slug) VALUES (:id, :org, 'P', :slug)"),
            {"id": project_id, "org": org_id, "slug": f"project-{index}"},
        )
        await db.execute(
            text(
                "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
                "VALUES (:project_id, md5(random()::text), now(), now())"
            ),
            {"project_id": project_id},
        )
    routed = projects[route_in_project] if route_in_project is not None else projects[0]
    if route_in_project is not None:
        await _insert_route(db, routed, credential_id)
    return Org(credential_id, (projects[0], projects[1], projects[2]), routed)


async def _build(session_factory: Sessions, *, route_in_project: int | None) -> Org:
    async with session_factory() as db:
        await bypass_rls(db)
        org = await _insert_org(db, route_in_project=route_in_project)
        await db.commit()
    return org


async def _visible_projects(db: AsyncSession) -> set[uuid.UUID]:
    return set((await db.scalars(_TRACE_PROJECTS)).all())


async def test_the_scan_finds_a_route_in_another_project_and_keeps_the_binding(
    session_factory: Sessions,
) -> None:
    org = await _build(session_factory, route_in_project=2)
    bound = org.projects[0]  # the scan visits all three projects, ending on the last
    async with session_factory() as db:
        await bind_project(db, bound)

        in_use = await queries.credential_in_use(db, org.credential_id)

        assert in_use == "main"
        assert await db.scalar(_CURRENT_BINDING) == str(bound)
        assert await _visible_projects(db) == {bound}


async def test_the_scan_that_finds_nothing_keeps_the_binding(session_factory: Sessions) -> None:
    org = await _build(session_factory, route_in_project=None)
    bound = org.projects[1]
    async with session_factory() as db:
        await bind_project(db, bound)

        assert await queries.credential_in_use(db, org.credential_id) is None

        assert await db.scalar(_CURRENT_BINDING) == str(bound)
        assert await _visible_projects(db) == {bound}


async def test_the_scan_leaves_an_unbound_session_unbound(session_factory: Sessions) -> None:
    org = await _build(session_factory, route_in_project=0)
    async with session_factory() as db:
        assert await queries.credential_in_use(db, org.credential_id) == "main"

        assert not await db.scalar(_CURRENT_BINDING)
        assert await _visible_projects(db) == set()


async def test_a_scan_that_fails_midway_keeps_the_binding(
    session_factory: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _build(session_factory, route_in_project=2)
    bound = org.projects[0]
    binds: list[uuid.UUID] = []

    async def bind_then_fail(session: AsyncSession, project_id: uuid.UUID) -> None:
        binds.append(project_id)
        await bind_project(session, project_id)
        if len(binds) == 2:
            raise RuntimeError("scan interrupted")

    monkeypatch.setattr(queries, "bind_project", bind_then_fail)
    async with session_factory() as db:
        await bind_project(db, bound)

        with pytest.raises(RuntimeError, match="scan interrupted"):
            await queries.credential_in_use(db, org.credential_id)

        # The scan had bound the second project when it failed; the caller's binding is back.
        assert binds == list(org.projects[:2])
        assert await db.scalar(_CURRENT_BINDING) == str(bound)
        assert await _visible_projects(db) == {bound}
