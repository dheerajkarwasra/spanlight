"""`provider_incident`: a provider failing across the organization.

Reads `ctx.org_provider_errors` (the last 30 minutes, every project of the organization,
fault-injected spans already excluded by the engine's query) and the current project's own
llm spans. Per provider it sums `provider_5xx` and `calls` over all projects. It fires when
the provider has at least 20 5xx errors and a share of at least 20 % of its calls, spread over
at least 2 projects or at least 50 errors in one. The finding is emitted into the current
project only when that project had at least one 5xx from the provider itself. Warning;
critical at a share of 50 % or more. Key: `{provider}`. Evidence trace ids are the current
project's own non-fault `provider_5xx` llm spans of the provider in the window.

The finding cites the newest of `ctx.provider_incidents` for the provider that overlaps the
window (`started_at < end` and `(resolved_at or now) + DETECTOR_GRACE > start`) in
four `incident_*` metrics (all `None` without one) and an `incident_note` param. The citation
only explains: thresholds, severity and the key ignore it, so a misleading status page can
neither open nor silence a finding.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.insights import format as fmt
from app.insights.context import DetectorContext, LlmSpanRow, OrgProviderRow
from app.insights.detectors._evidence import by_key, span_trace_ids
from app.insights.schemas import Evidence, Finding, MetricValue, Severity
from app.provider_status.types import DETECTOR_GRACE, ProviderIncidentRow

KIND = "provider_incident"
WINDOW = timedelta(minutes=30)
MIN_ORG_ERRORS = 20
MIN_ORG_SHARE = Decimal("0.20")
MIN_PROJECTS_AFFECTED = 2
SOLO_PROJECT_MIN_ERRORS = 50
CRITICAL_SHARE = Decimal("0.50")
PROVIDER_5XX = "provider_5xx"
NO_INCIDENT_NOTE = "No matching incident on the provider's status page."


@dataclass(frozen=True, slots=True)
class _ProviderTotals:
    org_errors: int
    calls: int
    projects_affected: int

    @property
    def org_share(self) -> Decimal | None:
        return Decimal(self.org_errors) / Decimal(self.calls) if self.calls > 0 else None


def _totals_by_provider(rows: tuple[OrgProviderRow, ...]) -> dict[str, _ProviderTotals]:
    grouped: dict[str, list[OrgProviderRow]] = defaultdict(list)
    for row in rows:
        grouped[row.provider].append(row)
    return {
        provider: _ProviderTotals(
            org_errors=sum(row.provider_5xx for row in provider_rows),
            calls=sum(row.calls for row in provider_rows),
            projects_affected=sum(1 for row in provider_rows if row.provider_5xx >= 1),
        )
        for provider, provider_rows in grouped.items()
    }


def _is_incident(totals: _ProviderTotals) -> bool:
    share = totals.org_share
    if share is None or totals.org_errors < MIN_ORG_ERRORS or share < MIN_ORG_SHARE:
        return False
    return (
        totals.projects_affected >= MIN_PROJECTS_AFFECTED
        or totals.org_errors >= SOLO_PROJECT_MIN_ERRORS
    )


def _own_failure_trace_ids(ctx: DetectorContext, provider: str) -> list[str]:
    """Distinct traces of this project's non-fault 5xx spans on the provider, newest first."""
    failed: list[LlmSpanRow] = [
        span
        for span in ctx.llm_spans
        if span.provider == provider
        and span.error_class == PROVIDER_5XX
        and span.fault_scenario is None
        and ctx.window.contains(span.started_at)
    ]
    return span_trace_ids(failed)


def _overlaps(ctx: DetectorContext, incident: ProviderIncidentRow) -> bool:
    end = ctx.now if incident.resolved_at is None else incident.resolved_at
    return incident.started_at < ctx.window.end and end + DETECTOR_GRACE > ctx.window.start


def _cited_incident(ctx: DetectorContext, provider: str) -> ProviderIncidentRow | None:
    """The provider's newest incident overlapping the window, by `(started_at, id)`. Providers
    match case-insensitively (a span's `OpenAI` is the feed's `openai`); `api_affected` is
    already filtered by the loader, as the row does not carry it."""
    overlapping = [
        incident
        for incident in ctx.provider_incidents
        if incident.provider.lower() == provider.lower() and _overlaps(ctx, incident)
    ]
    return max(overlapping, key=lambda incident: (incident.started_at, incident.id), default=None)


def _incident_metrics(incident: ProviderIncidentRow | None) -> dict[str, MetricValue]:
    if incident is None:
        return dict.fromkeys(
            ("incident_id", "incident_title", "incident_url", "incident_impact"), None
        )
    return {
        "incident_id": str(incident.id),
        "incident_title": incident.title,
        "incident_url": incident.url,
        "incident_impact": incident.impact,
    }


def _incident_note(incident: ProviderIncidentRow | None) -> str:
    if incident is None:
        return NO_INCIDENT_NOTE
    return f"Matches the provider's status page incident “{incident.title}” ({incident.impact})."


def _finding(
    ctx: DetectorContext, provider: str, totals: _ProviderTotals, share: Decimal
) -> Finding:
    severity = Severity.CRITICAL if share >= CRITICAL_SHARE else Severity.WARNING
    incident = _cited_incident(ctx, provider)
    return Finding(
        kind=KIND,
        severity=severity,
        fingerprint_key=provider,
        evidence=Evidence(
            trace_ids=_own_failure_trace_ids(ctx, provider),
            metrics={
                "org_errors": totals.org_errors,
                "org_share": fmt.round_share(share),
                "projects_affected": totals.projects_affected,
                **_incident_metrics(incident),
            },
            window=ctx.window,
        ),
        params={
            "provider": fmt.text(provider),
            "org_errors": fmt.count(totals.org_errors),
            "org_share": fmt.rate(share),
            "projects_affected": fmt.count_noun(totals.projects_affected, "project"),
            "incident_note": _incident_note(incident),
        },
    )


@dataclass(frozen=True, slots=True)
class _ProviderIncident:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = MIN_ORG_ERRORS

    def run(self, ctx: DetectorContext) -> list[Finding]:
        own_errors = {
            row.provider
            for row in ctx.org_provider_errors
            if row.project_id == ctx.project_id and row.provider_5xx >= 1
        }
        totals = _totals_by_provider(tuple(ctx.org_provider_errors))
        return by_key(
            _finding(ctx, provider, provider_totals, share)
            for provider, provider_totals in totals.items()
            if provider in own_errors
            and _is_incident(provider_totals)
            and (share := provider_totals.org_share) is not None
        )


detector = _ProviderIncident()
