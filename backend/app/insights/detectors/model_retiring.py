"""`model_retiring`: calls to a model whose provider has announced its retirement.

Reads the llm spans of the last 24 hours. Fault-injected spans and gateway cache hits are
dropped before counting. Each span's provider and model are looked up in the bundled
retirement table (`app.pricing.retirements`, first-party providers only, `-latest` never
matches a snapshot), and spans are grouped by the matched row. One call is enough: a group
opens a finding when the model retires within 60 days, as a warning, and as critical from 14
days out and after the date (calls then fail). Key: `{provider}:{model_pattern}`.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from app.insights import format as fmt
from app.insights.context import DetectorContext, LlmSpanRow
from app.insights.detectors._evidence import by_key, span_trace_ids
from app.insights.schemas import Evidence, Finding, Severity
from app.pricing.retirements import Retirement, retirement_for

KIND = "model_retiring"
WINDOW = timedelta(hours=24)
HORIZON_DAYS = 60
CRITICAL_DAYS = 14
TOP_NAMES = 5


def _top(values: Iterable[str | None]) -> str:
    """The most frequent names, most calls first (ties by name), or "—" when none is known."""
    counts = Counter(value for value in values if value)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_NAMES]
    return ", ".join(name for name, _ in ranked) or fmt.UNKNOWN


def _retires_in(days_left: int) -> str:
    if days_left == 0:
        return "today"
    if days_left > 0:
        return f"in {fmt.count_noun(days_left, 'day')}"
    return f"{fmt.count_noun(-days_left, 'day')} ago"


def _finding(
    ctx: DetectorContext, retirement: Retirement, spans: list[LlmSpanRow], days_left: int
) -> Finding:
    severity = Severity.CRITICAL if days_left <= CRITICAL_DAYS else Severity.WARNING
    return Finding(
        kind=KIND,
        severity=severity,
        fingerprint_key=f"{retirement.provider}:{retirement.model_pattern}",
        evidence=Evidence(
            trace_ids=span_trace_ids(spans),
            metrics={"calls": len(spans), "days_left": days_left},
            window=ctx.window,
        ),
        params={
            "provider": retirement.provider,
            "model": retirement.model_pattern,
            "calls": fmt.count_noun(len(spans), "call"),
            "retires_on": retirement.retires_on.isoformat(),
            "retires_in": _retires_in(days_left),
            "replacement": retirement.replacement,
            "routes": _top(span.gateway.route if span.gateway else None for span in spans),
            "trace_names": _top(span.trace_name for span in spans),
        },
    )


@dataclass(frozen=True, slots=True)
class _ModelRetiring:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = 1

    def run(self, ctx: DetectorContext) -> list[Finding]:
        groups: dict[Retirement, list[LlmSpanRow]] = defaultdict(list)
        for span in ctx.llm_spans:
            if span.fault_scenario is not None or span.is_cache_hit:
                continue
            retirement = retirement_for(span.provider, span.model)
            if retirement is not None:
                groups[retirement].append(span)
        today = ctx.now.date()
        findings = []
        for retirement, spans in groups.items():
            days_left = (retirement.retires_on - today).days
            if len(spans) >= self.min_samples and days_left <= HORIZON_DAYS:
                findings.append(_finding(ctx, retirement, spans, days_left))
        return by_key(findings)


detector = _ModelRetiring()
