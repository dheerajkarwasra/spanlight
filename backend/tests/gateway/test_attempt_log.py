"""The attempt log keeps every attempt of a short call and the first 9 and the last of a long one.

The first tests are pure: `attempt_log.build` reads `Attempt` records and a target-name map,
nothing else. The last ones run calls through `execute` against the fake provider and read the
log back from the stored span.
"""

import dataclasses
import random
import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ProviderCredential
from app.db.rls import bind_project
from app.gateway import attempt_log
from app.gateway.context import Attempt, GatewayContext
from app.gateway.execute import execute
from app.gateway.route_config import RetryPolicy, Target
from tests.gateway.support import chat_frames, chat_request, read_to_end, with_route
from tests.gateway_fixtures import OPENAI_API_KEY, GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import FakeProvider, read_gateway_span

CREDENTIAL = uuid.uuid4()
NAMES: dict[int, str | None] = {0: "openai-prod", 1: "anthropic-backup", 2: None}


def _attempts(count: int) -> list[Attempt]:
    """`count` attempts over three targets; attempt `i` lasts `i + 0.5` ms."""
    return [
        Attempt(
            target_index=i % 3,
            credential_id=CREDENTIAL,
            status=429 if i % 2 == 0 else None,
            error=None if i % 2 == 0 else "timeout",
            duration_ms=i + 0.5,
            retry_after=2.0 if i % 2 == 0 else None,
        )
        for i in range(count)
    ]


@pytest.mark.parametrize("count", [1, 3, 10])
def test_attempt_log_keeps_1_3_and_10_attempts_whole(count: int) -> None:
    attempts = _attempts(count)

    log = attempt_log.build(attempts, NAMES)

    assert [entry["duration_ms"] for entry in log] == [a.duration_ms for a in attempts]
    assert log[0] == {
        "target": 0,
        "name": "openai-prod",
        "status": 429,
        "error": None,
        "duration_ms": 0.5,
        "retry_after_s": 2.0,
    }
    if count >= 3:
        assert log[1] == {
            "target": 1,
            "name": "anthropic-backup",
            "status": None,
            "error": "timeout",
            "duration_ms": 1.5,
            "retry_after_s": None,
        }
        assert log[2]["name"] is None  # a target whose credential is unknown


def test_12_attempts_keep_first_9_and_last() -> None:
    log = attempt_log.build(_attempts(12), NAMES)

    assert len(log) == attempt_log.MAX_ENTRIES == 10
    assert [entry["duration_ms"] for entry in log] == [i + 0.5 for i in [*range(9), 11]]


def test_40_attempts_keep_first_9_and_last() -> None:
    log = attempt_log.build(_attempts(40), NAMES)

    assert len(log) == 10
    assert [entry["duration_ms"] for entry in log] == [i + 0.5 for i in [*range(9), 39]]


def test_no_attempts_is_an_empty_log() -> None:
    assert attempt_log.build([], NAMES) == []


def test_a_target_missing_from_the_names_has_no_name() -> None:
    attempt = Attempt(7, CREDENTIAL, 200, None, 12.0)

    assert attempt_log.build([attempt], NAMES)[0]["name"] is None


OVERLOADED = {"error": {"message": "Overloaded.", "type": "server_error", "code": None}}


async def _no_wait(seconds: float) -> None:
    """Retries in these tests do not wait."""


class _FirstTargetFirst(random.Random):
    """The weighted first pick always lands on the route's first target, so the order is fixed."""

    def choices(self, population: Sequence[Any], *args: Any, **kwargs: Any) -> list[Any]:
        return [population[0]]


async def _with_backup_openai(
    context: GatewayContext,
    workspace: GatewayWorkspace,
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[GatewayContext, Target]:
    """`context` that also knows a second OpenAI credential, named `openai-backup`, and its target.

    A fallback never crosses provider formats (`routing.compatible_targets`), so a chat call
    falls back from one OpenAI-style credential to another.
    """
    response = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/credentials",
        json={"name": "openai-backup", "provider": "openai", "api_key": OPENAI_API_KEY},
    )
    assert response.status_code == 201, response.text
    credential_id = uuid.UUID(response.json()["id"])
    async with sessions() as db, db.begin():
        await bind_project(db, workspace.project_id)
        backup = await db.get(ProviderCredential, credential_id)
    assert backup is not None
    credentials = {**context.credentials, backup.id: backup}
    return dataclasses.replace(context, credentials=credentials), Target(credential_id=backup.id)


async def test_fallback_log_shows_two_targets(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(OVERLOADED, status=503, headers={"retry-after": "0"})
    fake_provider.reply_stream(chat_frames())
    context, backup = await _with_backup_openai(
        await gateway_context(), gateway_workspace, session_factory
    )
    primary = Target(credential_id=gateway_workspace.openai_credential_id)
    context = with_route(context, targets=[primary, backup], retry=RetryPolicy(max_attempts=1))
    context = dataclasses.replace(context, rng=_FirstTargetFirst())

    result = await execute(chat_request(), context)
    assert result.stream is not None
    await read_to_end(result.stream)

    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    attributes: dict[str, Any] = span["attributes"]
    log = attributes["spanlight.gateway.attempt_log"]
    assert [(entry["target"], entry["name"], entry["status"]) for entry in log] == [
        (0, "openai", 503),
        (1, "openai-backup", 200),
    ]
    assert attributes["spanlight.gateway.fallbacks"] == 1
    assert attributes["spanlight.gateway.target"] == "openai-backup"


async def test_attempt_log_after_12_attempts_has_10_entries_and_attempts_12(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(12):
        fake_provider.reply_json(OVERLOADED, status=503, headers={"retry-after": "0"})
    context, backup = await _with_backup_openai(
        await gateway_context(), gateway_workspace, session_factory
    )
    primary = Target(credential_id=gateway_workspace.openai_credential_id)
    retry = RetryPolicy(max_attempts=4, backoff_ms=0, max_backoff_ms=0)
    context = with_route(context, targets=[primary, backup, primary], retry=retry)
    context = dataclasses.replace(context, sleep=_no_wait, rng=_FirstTargetFirst())

    result = await execute(chat_request(stream=False), context)

    assert result.status == 503
    assert fake_provider.pending == 0
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    attributes: dict[str, Any] = span["attributes"]
    log = attributes["spanlight.gateway.attempt_log"]
    assert attributes["spanlight.gateway.attempts"] == 12
    assert len(log) == 10
    # The first 9 attempts (4 on target 0, 4 on target 1, 1 on target 2), then the 12th.
    assert [entry["target"] for entry in log] == [0, 0, 0, 0, 1, 1, 1, 1, 2, 2]
    assert [entry["name"] for entry in log][3:5] == ["openai", "openai-backup"]
    assert all(entry["status"] == 503 for entry in log)
