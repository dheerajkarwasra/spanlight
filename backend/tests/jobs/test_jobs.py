"""The lease/fence job queue, the worker loop, scheduling and maintenance tasks."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import (
    EmailToken,
    EmailTokenKind,
    Job,
    JobStatus,
    LoginAttempt,
    Session,
    Span,
    ThrottleEvent,
    Trace,
    User,
)
from app.db.rls import bypass_rls
from app.jobs import queue
from app.jobs.context import TaskContext
from app.jobs.scheduler import (
    DELIVER_NOTIFICATIONS,
    DEMO_TRAFFIC,
    RETENTION,
    period_key,
    schedule_periodic,
)
from app.jobs.tasks.cleanup import cleanup
from app.jobs.tasks.retention import apply_retention
from app.jobs.worker import Worker
from tests.gateway_fixtures import CREDENTIALS_KEYS
from tests.gateway_support import logged
from tests.helpers import Browser, bearer, create_workspace, new_trace_id, span

SessionFactory = async_sessionmaker[AsyncSession]


async def _expire_lease(session_factory: SessionFactory, job_id: int) -> None:
    async with session_factory() as db:
        await db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(lease_until=func.now() - timedelta(seconds=1))
        )
        await db.commit()


async def _job(session_factory: SessionFactory, job_id: int) -> Job:
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        return job


async def test_claim_complete(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        assert await queue.enqueue(db, "noop", {"x": 1})
        await db.commit()
        claimed = await queue.claim_next(db)
        assert claimed is not None
        assert (claimed.kind, claimed.payload, claimed.attempts, claimed.fence) == (
            "noop",
            {"x": 1},
            1,
            1,
        )
        assert await queue.claim_next(db) is None  # leased, so not claimable again
        await queue.complete(db, claimed)

    assert (await _job(session_factory, claimed.id)).status is JobStatus.DONE


async def test_expired_lease_is_reclaimed_and_stale_worker_is_fenced_out(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        await queue.enqueue(db, "slow")
        await db.commit()
        first = await queue.claim_next(db)
    assert first is not None
    await _expire_lease(session_factory, first.id)

    async with session_factory() as db:
        second = await queue.claim_next(db)
    assert second is not None
    assert second.id == first.id and second.fence == first.fence + 1 and second.attempts == 2

    async with session_factory() as db:
        with pytest.raises(queue.LeaseLostError):
            await queue.complete(db, first)
        with pytest.raises(queue.LeaseLostError):
            await queue.extend_lease(db, first)
        await queue.complete(db, second)
    assert (await _job(session_factory, first.id)).status is JobStatus.DONE


async def test_failure_retries_with_backoff_then_gives_up(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        await queue.enqueue(db, "flaky", max_attempts=2)
        await db.commit()
        claimed = await queue.claim_next(db)
        assert claimed is not None
        assert await queue.fail(db, claimed, "boom") is JobStatus.QUEUED
        assert await queue.claim_next(db) is None  # backing off

        await db.execute(update(Job).values(run_after=func.now()))
        await db.commit()
        retried = await queue.claim_next(db)
        assert retried is not None and retried.attempts == 2
        assert await queue.fail(db, retried, "boom again") is JobStatus.FAILED

    job = await _job(session_factory, claimed.id)
    assert job.status is JobStatus.FAILED and job.last_error == "boom again"


async def test_lease_expiry_on_final_attempt_marks_failed(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        await queue.enqueue(db, "crashy", max_attempts=1)
        await db.commit()
        claimed = await queue.claim_next(db)
    assert claimed is not None
    await _expire_lease(session_factory, claimed.id)

    async with session_factory() as db:
        assert await queue.claim_next(db) is None
    assert (await _job(session_factory, claimed.id)).status is JobStatus.FAILED


def test_period_key_has_second_precision_only_for_periods_under_a_minute() -> None:
    moment = datetime(2026, 10, 7, 13, 10, 41, tzinfo=UTC)
    # A minute-precision key would collapse a 30-second job to one run per minute.
    assert period_key(DELIVER_NOTIFICATIONS, moment) == "deliver_notifications:2026-10-07T13:10:30"
    assert (
        period_key(DELIVER_NOTIFICATIONS, moment + timedelta(seconds=19))
        == "deliver_notifications:2026-10-07T13:11:00"
    )
    # Existing keys must not change, or a deploy would enqueue a duplicate run of each job.
    assert period_key(RETENTION, moment) == "retention:2026-10-07T13:00"
    assert period_key(DEMO_TRAFFIC, moment) == "demo_traffic:2026-10-07T13:00"
    assert (
        period_key(DEMO_TRAFFIC, moment + timedelta(minutes=20)) == "demo_traffic:2026-10-07T13:30"
    )


def test_backoff_is_exponential_and_capped() -> None:
    assert queue.retry_backoff(1) == timedelta(seconds=15)
    assert queue.retry_backoff(3) == timedelta(seconds=60)
    assert queue.retry_backoff(20) == timedelta(hours=1)


async def test_worker_runs_handlers_and_records_failures(
    session_factory: SessionFactory, settings: Settings
) -> None:
    seen: list[dict[str, Any]] = []

    async def succeed(context: TaskContext, payload: dict[str, Any]) -> None:
        await context.heartbeat()
        seen.append(payload)

    async def explode(_: TaskContext, __: dict[str, Any]) -> None:
        raise RuntimeError("kaboom")

    async with session_factory() as db:
        await queue.enqueue(db, "good", {"n": 1})
        await queue.enqueue(db, "bad")
        await queue.enqueue(db, "unknown")
        await db.commit()

    worker = Worker(session_factory, settings, handlers={"good": succeed, "bad": explode})
    assert [await worker.run_once() for _ in range(4)] == [True, True, True, False]
    assert seen == [{"n": 1}]

    async with session_factory() as db:
        jobs = {job.kind: job for job in (await db.scalars(select(Job))).all()}
    assert jobs["good"].status is JobStatus.DONE
    assert jobs["bad"].status is JobStatus.QUEUED
    assert jobs["bad"].last_error == "RuntimeError: kaboom"
    assert "no handler" in (jobs["unknown"].last_error or "")


async def _start_and_stop(
    session_factory: SessionFactory, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """Start a worker that is told to stop at once, so only its start-up runs."""
    caplog.set_level(logging.INFO)
    stop = asyncio.Event()
    stop.set()
    await Worker(session_factory, settings, handlers={}).run_forever(stop)


async def test_a_worker_without_credentials_keys_warns_at_start_up(
    session_factory: SessionFactory, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    assert not settings.is_crypto_configured

    await _start_and_stop(session_factory, settings, caplog)

    [started] = logged(caplog, "worker_started")
    assert started["crypto_configured"] is False
    [warning] = logged(caplog, "worker_credentials_keys_missing")
    assert warning["level"] == "warning"
    assert "CREDENTIALS_KEYS" in warning["impact"]


async def test_a_worker_with_credentials_keys_starts_without_the_warning(
    session_factory: SessionFactory, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    keyed = settings.model_copy(update={"credentials_keys": SecretStr(CREDENTIALS_KEYS)})

    await _start_and_stop(session_factory, keyed, caplog)

    [started] = logged(caplog, "worker_started")
    assert started["crypto_configured"] is True
    assert logged(caplog, "worker_credentials_keys_missing") == []


async def test_scheduler_dedupes_per_period(
    session_factory: SessionFactory, settings: Settings
) -> None:
    now = datetime(2026, 10, 7, 13, 10, tzinfo=UTC)
    async with session_factory() as db:
        first = await schedule_periodic(db, settings, now)
        same_period = await schedule_periodic(db, settings, now + timedelta(seconds=20))
        again = await schedule_periodic(db, settings, now + timedelta(minutes=5))
        later = await schedule_periodic(db, settings, now + timedelta(minutes=25))
    # Every periodic job the test settings enable (demo, alerts, digest, detectors, user stats).
    assert sorted(first) == [
        "backup_database",
        "cleanup_sessions",
        "deliver_notifications",
        "demo_traffic",
        "evaluate_alerts",
        "prune_gateway_cache",
        "refresh_user_stats",
        "retention",
        "rollup_hourly",
        "run_detectors",
        "sample_for_evaluation",
        "weekly_digest",
    ]
    assert same_period == []
    # Five minutes on (13:15): new periods for the 30-second, 1-minute, 5-minute and 15-minute
    # jobs; the hourly, daily, weekly and 30-minute jobs are already queued.
    assert sorted(again) == [
        "deliver_notifications",
        "evaluate_alerts",
        "refresh_user_stats",
        "rollup_hourly",
        "run_detectors",
        "sample_for_evaluation",
    ]
    # At 13:35 a new 30-minute period for demo traffic too.
    assert sorted(later) == [
        "deliver_notifications",
        "demo_traffic",
        "evaluate_alerts",
        "refresh_user_stats",
        "rollup_hourly",
        "run_detectors",
        "sample_for_evaluation",
    ]

    async with session_factory() as db:
        demo = await db.scalar(select(Job).where(Job.kind == "demo_traffic").limit(1))
    assert demo is not None and demo.max_attempts == 1


async def test_retention_deletes_only_expired_traces(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    short = await create_workspace(owner, org_name="Short")
    long = await create_workspace(owner, org_name="Long")
    await owner.patch(f"/api/v1/projects/{short.project_id}", json={"retention_days": 1})

    now = datetime.now(UTC)
    for workspace in (short, long):
        response = await client.post(
            "/v1/traces",
            json={
                "spans": [
                    span(trace_id=new_trace_id(), start=now - timedelta(days=3)),
                    span(trace_id=new_trace_id(), start=now - timedelta(hours=2)),
                ]
            },
            headers=bearer(workspace.api_key),
        )
        assert response.json()["accepted"] == 2

    deleted = await apply_retention(session_factory, now=now)
    assert deleted == 1

    async with session_factory() as db:
        await bypass_rls(db)
        remaining = (
            await db.execute(select(Trace.project_id, func.count()).group_by(Trace.project_id))
        ).all()
        span_count = await db.scalar(select(func.count()).select_from(Span))
    counts = {str(project_id): count for project_id, count in remaining}
    assert counts == {short.project_id: 1, long.project_id: 2}
    assert span_count == 3  # spans cascade with their trace


async def test_cleanup_removes_expired_sessions_and_old_rows(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    live: Browser = await browser_factory("live@example.com")
    await browser_factory("stale@example.com")
    now = datetime.now(UTC)
    async with session_factory() as db:
        await db.execute(
            update(Session)
            .where(Session.user_id != live.user["id"])
            .values(expires_at=now - timedelta(minutes=1))
        )
        db.add(
            LoginAttempt(
                email="x@example.com", ip=None, succeeded=False, created_at=now - timedelta(days=2)
            )
        )
        await queue.enqueue(db, "ancient")
        await db.execute(
            text("UPDATE jobs SET status = 'done', created_at = now() - interval '8 days'")
        )
        await db.commit()

    counts = await cleanup(session_factory, now=now)
    assert counts == {
        "sessions_deleted": 1,
        "login_attempts_deleted": 1,
        "jobs_deleted": 1,
        "outbox_rows_deleted": 0,
        "throttle_events_deleted": 0,
        "email_tokens_deleted": 0,
        "idempotency_keys_deleted": 0,
        "rate_limit_buckets_deleted": 0,
    }
    assert (await live.get("/api/v1/auth/me")).status_code == 200


async def test_cleanup_prunes_old_throttle_events_and_spent_email_tokens(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    old, recent = now - timedelta(hours=25), now - timedelta(hours=23)

    def token(
        user: User, label: bytes, created_at: datetime, *, expires_in: timedelta, used: bool
    ) -> EmailToken:
        return EmailToken(
            user_id=user.id,
            kind=EmailTokenKind.RESET,
            token_hash=label.ljust(32, b"-"),
            created_at=created_at,
            expires_at=created_at + expires_in,
            used_at=created_at if used else None,
        )

    async with session_factory() as db:
        user = User(email="ada@example.com", password_hash="!unusable", name="Ada")
        db.add(user)
        await db.flush()
        db.add_all(
            [
                ThrottleEvent(scope="email_verify", key="old", created_at=old),
                ThrottleEvent(scope="email_verify", key="recent", created_at=recent),
                token(user, b"used-old", old, expires_in=timedelta(days=9), used=True),
                token(user, b"expired-old", old, expires_in=timedelta(hours=1), used=False),
                # Younger than a day: kept, so a reset link that was just used still reads as used.
                token(user, b"used-recent", recent, expires_in=timedelta(hours=1), used=True),
                token(user, b"expired-recent", recent, expires_in=timedelta(hours=1), used=False),
                # Never pruned while it can still be used, however old.
                token(user, b"live-old", old, expires_in=timedelta(days=9), used=False),
            ]
        )
        await db.commit()

    counts = await cleanup(session_factory, now=now)

    assert counts["throttle_events_deleted"] == 1
    assert counts["email_tokens_deleted"] == 2
    async with session_factory() as db:
        events = set((await db.scalars(select(ThrottleEvent.key))).all())
        kept = {row.rstrip(b"-") for row in (await db.scalars(select(EmailToken.token_hash))).all()}
    assert events == {"recent"}
    assert kept == {b"used-recent", b"expired-recent", b"live-old"}


async def test_cleanup_prunes_expired_idempotency_keys_only(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as db:
        await db.execute(
            text(
                "INSERT INTO idempotency_keys (principal_id, key, request_hash, status, body, "
                "created_at, expires_at) VALUES "
                "('user:a', 'expired', :hash, 201, '{}', :old, :just_expired), "
                "('user:a', 'abandoned', :hash, NULL, NULL, :old, :just_expired), "
                "('user:a', 'live', :hash, 201, '{}', :recent, :later)"
            ),
            {
                "hash": b"\x01" * 32,
                "old": now - timedelta(hours=25),
                "just_expired": now - timedelta(minutes=1),
                "recent": now - timedelta(hours=1),
                "later": now + timedelta(hours=23),
            },
        )
        await db.commit()

    counts = await cleanup(session_factory, now=now)

    assert counts["idempotency_keys_deleted"] == 2
    async with session_factory() as db:
        kept = (await db.execute(text("SELECT key FROM idempotency_keys"))).scalars().all()
    assert kept == ["live"]
