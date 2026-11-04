"""Refused writes are audited, but one member cannot flood the chain by looping on a refusal.

Each refused POST used to commit an `access.denied` row and take the org's audit chain lock. A
viewer in a loop could grow the chain without bound and serialise every audited write of the
org. Now a member gets at most ten denied rows per org per fifteen minutes; the refusal itself
is unchanged, and other members are counted on their own.
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent
from tests.helpers import Browser, create_workspace, join_with_role

SessionFactory = async_sessionmaker[AsyncSession]


async def _denied_rows(factory: SessionFactory, user_id: str) -> int:
    async with factory() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "access.denied", AuditEvent.actor_user_id == user_id)
        )
    return int(count or 0)


async def test_a_member_gets_at_most_ten_denied_audit_rows_per_fifteen_minutes(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    viewer: Browser = await browser_factory("viewer@example.com")
    await join_with_role(owner, viewer, workspace.org_id, "viewer")
    other: Browser = await browser_factory("other-viewer@example.com")
    await join_with_role(owner, other, workspace.org_id, "viewer")

    url = f"/api/v1/orgs/{workspace.org_id}/projects"
    for _ in range(11):
        assert (await viewer.post(url, json={"name": "Nope"})).status_code == 403
    assert await _denied_rows(session_factory, viewer.user["id"]) == 10

    # Another member's refusal is still recorded: the limit is per member, not per org.
    assert (await other.post(url, json={"name": "Nope"})).status_code == 403
    assert await _denied_rows(session_factory, other.user["id"]) == 1
