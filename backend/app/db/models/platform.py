"""Platform plumbing: the job queue, idempotency keys, rate limit buckets and worker heartbeats."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, Integer, LargeBinary, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, _pg_enum


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"
    # Storage parameters; migration 1046 is the source of truth.
    __table_args__ = (
        {
            "postgresql_with": {
                "autovacuum_vacuum_scale_factor": "0.02",
                "autovacuum_analyze_scale_factor": "0.02",
            }
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    # How a `done` job ended (see `app.jobs.outcome.JobOutcome`); NULL until then.
    outcome: Mapped[str | None] = mapped_column(Text)
    status: Mapped[JobStatus] = mapped_column(
        _pg_enum(JobStatus, "job_status"), server_default=text("'queued'")
    )
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyKey(Base):
    """What a retried POST needs to be answered with the first answer instead of running again.

    One row per (principal, key). It is inserted, with no outcome, before the request runs and
    completed with the answer afterwards; a row without a `status` is a request still in flight.
    See `app.core.idempotency` for the rules and migration 0010 for the constraints.
    """

    __tablename__ = "idempotency_keys"

    # `user:<id>` for a session or a personal access token, `key:<id>` for an API key.
    principal_id: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    # SHA-256 of the method, the path and the canonical body.
    request_hash: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[int | None] = mapped_column(Integer)
    # `none_as_null`: no body is SQL NULL here, not the JSON value `null`.
    body: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    content_type: Mapped[str | None] = mapped_column(Text)
    # Also the owner's token: whoever took the row over wrote it (see `idempotency_store`).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RateLimitBucket(Base):
    """The tokens left in one caller's rate limit bucket, shared by every API replica.

    Never written through the ORM: `app.core.ratelimit` takes a token with a single upsert. The
    model exists so the schema is described in one place and the cleanup job can prune idle rows.
    The table is `UNLOGGED` (see migration 0012), which the ORM cannot express.
    """

    __tablename__ = "rate_limit_buckets"

    # `ingest:key:<id>`, `demo:ip:<ip>`, `api:token:<id>` or `api:key:<id>`.
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    tokens: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    """The last time one worker process said it was alive, read by `/health/ready`.

    One row per process (see `app.jobs.worker_heartbeat`); the newest `last_seen_at` is what counts.
    See migration 0015.
    """

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[str] = mapped_column(Text)
