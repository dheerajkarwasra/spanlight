"""error_spike: the error rate of one (environment, model) jumped well past its 7-day baseline.

Reads: llm spans of the last 15 minutes, after dropping fault-injected spans and the gateway
key limiter's own refusals (`provider_traffic`: no model failed), and the baseline
`error_rate` for the group's environment and model (any provider).
Rule: per (environment, model) with at least 50 calls, `error_rate >= max(3 x baseline,
baseline + 0.05)`, which also means `error_rate >= 0.05`. Warning; critical at `error_rate >= 0.25`.
Guards: fault exclusion before counting (min_samples included), no baseline means no finding, an
error is a span with `status == "error"`.
Key: `{environment}:{model}`.
"""

from datetime import timedelta
from decimal import Decimal

from app.insights import format as fmt
from app.insights.context import DetectorContext, LlmSpanRow
from app.insights.detectors._evidence import by_key, key_part, span_trace_ids
from app.insights.detectors.baselines import (
    exceeds,
    group_spans,
    provider_traffic,
)
from app.insights.schemas import Evidence, Finding, Severity

KIND = "error_spike"
WINDOW = timedelta(minutes=15)
MIN_CALLS = 50
BASELINE_MULTIPLE = Decimal(3)
BASELINE_FLOOR = Decimal("0.05")
CRITICAL_RATE = Decimal("0.25")
ERROR_STATUS = "error"


class ErrorSpikeDetector:
    kind = KIND
    window = WINDOW
    min_samples = MIN_CALLS

    def run(self, ctx: DetectorContext) -> list[Finding]:
        spans = provider_traffic(ctx.llm_spans)
        groups = group_spans(spans, lambda s: (s.environment, s.model))
        findings: list[Finding] = []
        for (environment, model), calls in groups.items():
            if len(calls) < MIN_CALLS:
                continue
            baseline = ctx.baselines.get("error_rate", environment=environment, model=model)
            if baseline is None:
                continue
            errors = [span for span in calls if span.status == ERROR_STATUS]
            error_rate = Decimal(len(errors)) / Decimal(len(calls))
            if not exceeds(error_rate, baseline, BASELINE_MULTIPLE, BASELINE_FLOOR):
                continue
            findings.append(_finding(ctx, environment, model, calls, errors, error_rate, baseline))
        return by_key(findings)


def _finding(
    ctx: DetectorContext,
    environment: str | None,
    model: str | None,
    calls: list[LlmSpanRow],
    errors: list[LlmSpanRow],
    error_rate: Decimal,
    baseline: Decimal,
) -> Finding:
    rate = fmt.round_share(error_rate)
    baseline_rate = fmt.round_share(baseline)
    return Finding(
        kind=KIND,
        severity=Severity.CRITICAL if error_rate >= CRITICAL_RATE else Severity.WARNING,
        fingerprint_key=f"{key_part(environment)}:{key_part(model)}",
        evidence=Evidence(
            trace_ids=span_trace_ids(errors),
            metrics={
                "error_rate": rate,
                "baseline_error_rate": baseline_rate,
                "calls": len(calls),
                "errors": len(errors),
            },
            window=ctx.window,
        ),
        params={
            "error_rate": fmt.rate(rate),
            "model": fmt.text(model),
            "environment": fmt.text(environment),
            "errors": fmt.count(len(errors)),
            "calls": fmt.count(len(calls)),
            "baseline_error_rate": fmt.rate(baseline_rate),
        },
    )


detector = ErrorSpikeDetector()
