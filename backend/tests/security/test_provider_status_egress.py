"""The status poller never connects to a private address, whatever DNS answers for a feed host.

The feed URLs are constants, so the only way to aim the poller at the internal network is DNS.
The resolver below answers `10.0.0.1` for `status.claude.com`: the poll must end `blocked`,
count it, and send nothing and write no incident.
"""

from collections.abc import Sequence

import httpx
import pytest

from app.config import Settings
from app.core.observability import PROVIDER_STATUS_POLLS
from app.provider_status import jobs
from app.provider_status.adapters.anthropic import anthropic
from app.provider_status.jobs import run_poll_provider_status
from app.provider_status.poller import PollOutcome, poll_one
from tests.gateway_support import metric_delta
from tests.provider_status.poll_support import (
    ANTHROPIC_FEED,
    NOW,
    PUBLIC_ADDRESS,
    FeedServer,
    SessionFactory,
    fixture,
    incidents,
    source,
    task_context,
)


async def _claude_is_private(host: str, port: int) -> Sequence[str]:
    return ["10.0.0.1"] if host == "status.claude.com" else [PUBLIC_ADDRESS]


async def test_a_private_answer_for_the_feed_host_is_blocked(
    session_factory: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FeedServer()
    server.serve_recorded_feeds()
    monkeypatch.setattr(jobs, "build_status_client", lambda: server.client(_claude_is_private))
    blocked = metric_delta(PROVIDER_STATUS_POLLS, provider="anthropic", outcome="blocked")

    await run_poll_provider_status(task_context(session_factory, settings), {})

    assert blocked.value == 1
    assert server.requests_to(anthropic.url) == []
    assert await incidents(session_factory, "anthropic") == []
    feed = await source(session_factory, "anthropic")
    assert (feed["last_outcome"], feed["last_error_type"]) == ("blocked", "BlockedAddress")
    assert feed["etag"] is None
    # The other feeds resolve publicly and were polled as usual.
    assert (await source(session_factory, "google"))["last_outcome"] == "fetched"


async def test_poll_one_reports_blocked(session_factory: SessionFactory) -> None:
    server = FeedServer()
    server.answer(anthropic.url, httpx.Response(200, content=fixture(ANTHROPIC_FEED)))

    async with server.client(_claude_is_private) as client, session_factory() as db:
        result = await poll_one(client, db, anthropic, now=NOW)

    assert result.outcome == PollOutcome.BLOCKED
    assert server.requests == []
