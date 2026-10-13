"""A rule that keeps an id of a channel that is gone, or of another organization's, still fires.

Deleting a channel does not touch the rules that name it, and `channel_ids` has no foreign key,
so a rule can hold an id that names no channel of its organization: a deleted one, or (through
bad data) another organization's. `notify_channels` queues deliveries only to the payload
organization's channels. The event is the record that the alert fired, so it must be written
whatever happens to the notification, and nothing may be queued to a channel the organization
does not own.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.jobs import evaluate_all
from app.config import Settings
from app.db.rls import bypass_rls
from tests.authz.reference_world import World

# `llm_calls < 1` over an empty window reads 0, so the rule fires on the first evaluation.
FIRING_RULE = {
    "name": "quiet",
    "kind": "threshold",
    "metric": "llm_calls",
    "comparator": "lt",
    "threshold": "1",
    "window_minutes": 5,
}

Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def app(gateway_app: Any) -> Any:
    """The app that `browser_factory` signs people in to: one that can seal credentials."""
    return gateway_app


async def add_rule(world: World, channel_ids: list[str]) -> str:
    url = f"/api/v1/projects/{world.a_project}/alert-rules"
    response = await world.a.post(url, json={**FIRING_RULE, "channel_ids": channel_ids})
    assert response.status_code == 201, response.text
    rule_id: str = response.json()["id"]
    return rule_id


async def evaluate(session_factory: Sessions, settings: Settings) -> None:
    run = await evaluate_all(session_factory, settings, now=datetime.now(UTC))
    assert run.errors == 0 and run.projects_failed == 0
    assert run.transitions == 1


async def events_of(session_factory: Sessions, rule_id: str) -> list[str]:
    """The states of the rule's alert events, read past row-level security."""
    async with session_factory() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text("SELECT state FROM alert_events WHERE rule_id = :rule_id"),
            {"rule_id": uuid.UUID(rule_id)},
        )
        return [row[0] for row in rows]


async def outbox_channels(session_factory: Sessions) -> list[uuid.UUID]:
    async with session_factory() as db:
        rows = await db.execute(text("SELECT channel_id FROM notification_outbox"))
        return [row[0] for row in rows]


async def test_a_live_channel_is_notified_when_the_rule_fires(
    world: World, session_factory: Sessions, settings: Settings
) -> None:
    """The control: the outbox does receive a delivery, so the empty ones below mean something."""
    rule_id = await add_rule(world, [world.a_channel])

    await evaluate(session_factory, settings)

    assert await events_of(session_factory, rule_id) == ["firing"]
    assert await outbox_channels(session_factory) == [uuid.UUID(world.a_channel)]


async def test_a_deleted_channel_does_not_stop_the_event_or_raise(
    world: World, session_factory: Sessions, settings: Settings
) -> None:
    rule_id = await add_rule(world, [world.a_channel])
    deleted = await world.a.delete(f"/api/v1/orgs/{world.a_org}/alert-channels/{world.a_channel}")
    assert deleted.status_code == 204, deleted.text

    await evaluate(session_factory, settings)

    assert await events_of(session_factory, rule_id) == ["firing"]
    assert await outbox_channels(session_factory) == []


async def test_another_orgs_channel_in_a_rule_gets_no_delivery(
    world: World, session_factory: Sessions, settings: Settings
) -> None:
    """Bad data, past the API check: the rule names B's channel as well as its own."""
    rule_id = await add_rule(world, [world.a_channel])
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text("UPDATE alert_rules SET channel_ids = :ids WHERE id = :id"),
            {
                "ids": [uuid.UUID(world.b_channel), uuid.UUID(world.a_channel)],
                "id": uuid.UUID(rule_id),
            },
        )
        await db.commit()

    await evaluate(session_factory, settings)

    assert await events_of(session_factory, rule_id) == ["firing"]
    assert await outbox_channels(session_factory) == [uuid.UUID(world.a_channel)]
