"""Load what the detectors of one project run read: `load_context`.

Five reads, each bounded: the project's llm spans and tool spans of the last 24 hours (newest
first, at most `SPAN_CAP` each), one grouped read of the hourly rollups for the baselines, the
organization's provider errors of the last 30 minutes, and the status-page incidents of those
providers. The first three run in a transaction bound to the project (row-level security
applies; the `project_id` filters are a second guard). The organization read spans projects, so
it runs with row-level security bypassed in a transaction of its own, and the project binding is
restored afterwards. The incidents (a global table, no row-level security) are read in that
restored project-bound transaction, never with the bypass.

The baseline period is the 7 hour-aligned days that end where the 24-hour detection window
starts, `[floor_hour(now - 24 h) - 7 d, floor_hour(now - 24 h))`, not the 7 days up to now: the
traffic being judged never dilutes its own baseline. The price is that a new project has no
baselines (so no spike detectors) until it has enough calls older than a day.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import RowMapping, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project
from app.db.rls import bind_project, bypass_rls
from app.insights.context import DetectorContext, LlmSpanRow, OrgProviderRow, ToolSpanRow
from app.insights.context_build import (
    BASELINE_PERIOD,
    GATEWAY_ATTRIBUTES,
    GATEWAY_PREFIX,
    BaselineGroup,
    blank_to_none,
    build_baselines,
    gateway_attrs,
)
from app.insights.schemas import Window
from app.provider_status import service as provider_status
from app.provider_status.types import DETECTOR_GRACE, ProviderIncidentRow
from app.rollups.compute import floor_hour
from app.rollups.queries import summed_histogram

CONTEXT_WINDOW = timedelta(hours=24)
"""How far back the span reads go: the longest detector window."""

ORG_PROVIDER_WINDOW = timedelta(minutes=30)
SPAN_CAP = 50_000
"""Most spans of each kind one run reads; hitting it marks the context truncated."""

_GATEWAY_COLUMNS = ",\n".join(
    f"s.attributes ->> '{attribute}' AS gw_{name}" for attribute, name in GATEWAY_ATTRIBUTES.items()
)

# One row more than the cap, so a full page tells "exactly the cap" from "more than the cap".
# Key-limit refusals (kind `gateway`) are left out before the cap: no detector judges Spanlight's
# own limiter, and a refusal flood must not push real calls out of the read.
_LLM_SPANS = text(
    f"""
    SELECT s.trace_id, s.span_id, t.session_id, t.external_user_id, t.environment, t.release,
           t.name AS trace_name, s.started_at, s.duration_ms, s.status, s.error_class,
           s.provider, s.model, s.input_tokens, s.output_tokens, s.cached_tokens, s.cost_usd,
           s.request_hash, s.finish_reason, s.status_message, s.rate_limit_kind,
           {_GATEWAY_COLUMNS},
           EXISTS (
               SELECT 1 FROM jsonb_object_keys(s.attributes) AS attribute_key
               WHERE starts_with(attribute_key, '{GATEWAY_PREFIX}')
           ) AS is_gateway
    FROM spans AS s
    JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id
    WHERE s.project_id = :project_id
      AND s.kind = 'llm'
      AND NOT s.usage_rollup
      AND s.rate_limit_kind IS DISTINCT FROM 'gateway'
      AND s.started_at >= :start
      AND s.started_at < :end
    ORDER BY s.started_at DESC, s.span_id DESC
    LIMIT :limit
    """
)

# The input is hashed in the database so the stored inputs (up to 1 MiB each) never cross the
# wire: SHA-256 of the jsonb text form, whose key order and whitespace jsonb already normalises.
# This is not byte-identical to the Python canonical JSON of `app.ingest.request_hash`, and it
# need not be: tool-span input hashes are only ever compared with each other. A missing input or
# a JSON null has no hash.
_TOOL_SPANS = text(
    """
    SELECT s.trace_id, s.span_id, t.session_id, s.started_at, s.name, s.status,
           CASE WHEN s.input IS NULL OR jsonb_typeof(s.input) = 'null' THEN NULL
                ELSE left(encode(sha256(convert_to(s.input::text, 'UTF8')), 'hex'), 32)
           END AS input_hash
    FROM spans AS s
    JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id
    WHERE s.project_id = :project_id
      AND s.kind = 'tool'
      AND s.started_at >= :start
      AND s.started_at < :end
    ORDER BY s.started_at DESC, s.span_id DESC
    LIMIT :limit
    """
)

# NULL dimensions stay their own group (`read_filtered_rollups` cannot select them).
_BASELINE_GROUPS = text(
    f"""
    SELECT environment, provider, model,
           sum(span_count)                    AS calls,
           sum(errors)                        AS errors,
           sum(cost_usd)                      AS cost_usd,
           array_agg(DISTINCT bucket_start) FILTER (WHERE span_count > 0) AS active_hours,
           {summed_histogram("latency_buckets")} AS latency_buckets
    FROM span_rollups_hourly
    WHERE project_id = :project_id
      AND kind = 'llm'
      AND bucket_start >= :start
      AND bucket_start < :end
    GROUP BY environment, provider, model
    """
)

# Fault-injected spans are lab scenarios, not provider health: excluded from both counts.
_ORG_PROVIDER_ERRORS = text(
    """
    SELECT s.project_id, s.provider,
           count(*)                                              AS calls,
           count(*) FILTER (WHERE s.error_class = 'provider_5xx') AS provider_5xx
    FROM spans AS s
    JOIN projects AS p ON p.id = s.project_id
    WHERE p.org_id = :org_id
      AND s.kind = 'llm'
      AND s.started_at >= :start
      AND s.started_at < :end
      AND coalesce(s.provider, '') <> ''
      AND NOT (s.attributes ? 'spanlight.fault.scenario')
    GROUP BY s.project_id, s.provider
    """
)


async def load_context(
    db: AsyncSession, project_id: uuid.UUID, now: datetime
) -> DetectorContext | None:
    """The project's detector context at `now`; None when the project is gone.

    Leaves the session in a transaction bound to the project; the caller commits.
    """
    await bind_project(db, project_id)
    org_id = await db.scalar(select(Project.org_id).where(Project.id == project_id))
    if org_id is None:
        return None
    window = Window(start=now - CONTEXT_WINDOW, end=now)
    llm_spans, llm_truncated = await _llm_spans(db, project_id, window)
    tool_spans, tool_truncated = await _tool_spans(db, project_id, window)
    baseline_end = _baseline_end(now)
    groups = await _baseline_groups(db, project_id, baseline_end)
    baselines = build_baselines(groups)
    await db.commit()
    org_rows = await _org_provider_errors(db, org_id, now)
    await bind_project(db, project_id)
    incidents = await _provider_incidents(db, org_rows, now)
    return DetectorContext(
        project_id=project_id,
        org_id=org_id,
        now=now,
        window=window,
        llm_spans=llm_spans,
        tool_spans=tool_spans,
        baselines=baselines,
        org_provider_errors=org_rows,
        truncated=llm_truncated or tool_truncated,
        provider_incidents=incidents,
    )


async def _llm_spans(
    db: AsyncSession, project_id: uuid.UUID, window: Window
) -> tuple[list[LlmSpanRow], bool]:
    params = {
        "project_id": project_id,
        "start": window.start,
        "end": window.end,
        "limit": SPAN_CAP + 1,
    }
    rows = (await db.execute(_LLM_SPANS, params)).mappings().all()
    return [_llm_row(row) for row in rows[:SPAN_CAP]], len(rows) > SPAN_CAP


def _llm_row(values: RowMapping) -> LlmSpanRow:
    gateway_values = {name: values[f"gw_{name}"] for name in GATEWAY_ATTRIBUTES.values()}
    duration = values["duration_ms"]
    return LlmSpanRow(
        trace_id=values["trace_id"],
        span_id=values["span_id"],
        session_id=blank_to_none(values["session_id"]),
        external_user_id=blank_to_none(values["external_user_id"]),
        environment=blank_to_none(values["environment"]),
        release=blank_to_none(values["release"]),
        started_at=values["started_at"],
        duration_ms=None if duration is None else round(duration),
        status=str(values["status"]),
        error_class=values["error_class"],
        provider=blank_to_none(values["provider"]),
        model=blank_to_none(values["model"]),
        input_tokens=values["input_tokens"],
        output_tokens=values["output_tokens"],
        cached_tokens=values["cached_tokens"],
        cost_usd=values["cost_usd"],
        request_hash=values["request_hash"],
        finish_reason=values["finish_reason"],
        status_message=values["status_message"],
        gateway=gateway_attrs(gateway_values, is_gateway=bool(values["is_gateway"])),
        rate_limit_kind=values["rate_limit_kind"],
        trace_name=blank_to_none(values["trace_name"]),
    )


async def _tool_spans(
    db: AsyncSession, project_id: uuid.UUID, window: Window
) -> tuple[list[ToolSpanRow], bool]:
    params = {
        "project_id": project_id,
        "start": window.start,
        "end": window.end,
        "limit": SPAN_CAP + 1,
    }
    rows = (await db.execute(_TOOL_SPANS, params)).all()
    spans = [
        ToolSpanRow(
            trace_id=row.trace_id,
            span_id=row.span_id,
            session_id=blank_to_none(row.session_id),
            started_at=row.started_at,
            name=row.name,
            input_hash=row.input_hash,
            status=str(row.status),
        )
        for row in rows[:SPAN_CAP]
    ]
    return spans, len(rows) > SPAN_CAP


def _baseline_end(now: datetime) -> datetime:
    """Where the baseline period ends: the hour the 24-hour detection window starts in."""
    return floor_hour(now - CONTEXT_WINDOW)


async def _baseline_groups(
    db: AsyncSession, project_id: uuid.UUID, end: datetime
) -> list[BaselineGroup]:
    """The rollup groups of the `BASELINE_PERIOD` ending at `end` (see the module docstring)."""
    params = {"project_id": project_id, "start": end - BASELINE_PERIOD, "end": end}
    rows = (await db.execute(_BASELINE_GROUPS, params)).all()
    return [
        BaselineGroup(
            environment=row.environment,
            provider=row.provider,
            model=row.model,
            calls=int(row.calls),
            errors=int(row.errors),
            cost_usd=row.cost_usd,
            latency_buckets=[int(count) for count in row.latency_buckets],
            active_hours=frozenset(row.active_hours or ()),
        )
        for row in rows
    ]


async def _org_provider_errors(
    db: AsyncSession, org_id: uuid.UUID, now: datetime
) -> Sequence[OrgProviderRow]:
    """Every project of the organization, per provider. Ends its own transaction (the bypass is
    local to it)."""
    await bypass_rls(db)
    params = {"org_id": org_id, "start": now - ORG_PROVIDER_WINDOW, "end": now}
    rows = (await db.execute(_ORG_PROVIDER_ERRORS, params)).all()
    await db.commit()
    return [
        OrgProviderRow(
            project_id=uuid.UUID(str(row.project_id)),
            provider=row.provider,
            calls=int(row.calls),
            provider_5xx=int(row.provider_5xx),
        )
        for row in rows
    ]


async def _provider_incidents(
    db: AsyncSession, org_rows: Sequence[OrgProviderRow], now: datetime
) -> list[ProviderIncidentRow]:
    """The incidents overlapping the organization read's window, for each provider with a 5xx in
    it (a provider without one cannot fire `provider_incident`), at most 20 per provider. Span
    providers keep their sender's case and the feeds' are lowercase: matched lowercased."""
    providers = sorted({row.provider.lower() for row in org_rows if row.provider_5xx >= 1})
    incidents: list[ProviderIncidentRow] = []
    for provider in providers:
        start = now - ORG_PROVIDER_WINDOW
        incidents += await provider_status.overlapping_api_incidents(
            db, provider=provider, start=start, end=now, now=now, grace=DETECTOR_GRACE
        )
    return incidents
