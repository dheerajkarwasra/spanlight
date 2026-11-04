"""error_spike: the error rate of one (environment, model) jumped well past its 7-day baseline.

Reads: llm spans of the last 24 hours, after dropping fault-injected spans and the gateway
key limiter's own refusals (`provider_traffic`: no model failed), and the baseline
`error_rate` for the group's environment and model (any provider).
Rule: per (environment, model) with at least 50 calls in the last 15 minutes, `error_rate >=
max(3 x baseline, baseline + 0.05)`, which also means `error_rate >= 0.05`. Warning; critical at
`error_rate >= 0.25`.
No baseline (a new or small project): per (environment, model) with at least 50 calls in the last
24 hours, `error_rate >= 0.25`. Warning only; the evidence's `baseline_error_rate` is `None`, the
params carry no `baseline_error_rate`, and the catalogue renders its no-baseline title and summary
("Error rate is ..." / "No baseline yet ...") instead of the comparison with the 7 days before.
Guards: fault exclusion before counting (min_samples included), an error is a span with
`status == "error"`.
Key: `{environment}:{model}`, with or without a baseline.
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
from app.insights.schemas import Evidence, Finding, Severity, Window

KIND = "error_spike"
WINDOW = timedelta(minutes=15)
NO_BASELINE_WINDOW = timedelta(hours=24)
NO_BASELINE_RATE = Decimal("0.25")
MIN_CALLS = 50
BASELINE_MULTIPLE = Decimal(3)
BASELINE_FLOOR = Decimal("0.05")
CRITICAL_RATE = Decimal("0.25")
ERROR_STATUS = "error"


class ErrorSpikeDetector:
    kind = KIND
    window = NO_BASELINE_WINDOW  # the longest it reads; the baseline rule slices to `WINDOW`
    min_samples = MIN_CALLS

    def run(self, ctx: DetectorContext) -> list[Finding]:
        recent = Window(start=ctx.window.end - WINDOW, end=ctx.window.end)
        findings: list[Finding] = []
        spans = provider_traffic(ctx.llm_spans)
        for (environment, model), day in group_spans(
            spans, lambda s: (s.environment, s.model)
        ).items():
            baseline = ctx.baselines.get("error_rate", environment=environment, model=model)
            # A baselined slice is judged on the last 15 minutes, one without on all 24 hours.
            window = ctx.window if baseline is None else recent
            calls = day if baseline is None else [s for s in day if recent.contains(s.started_at)]
            if len(calls) < MIN_CALLS:
                continue
            errors = [span for span in calls if span.status == ERROR_STATUS]
            error_rate = Decimal(len(errors)) / Decimal(len(calls))
            if _fires(error_rate, baseline):
                findings.append(
                    _finding(window, environment, model, calls, errors, error_rate, baseline)
                )
        return by_key(findings)


def _fires(error_rate: Decimal, baseline: Decimal | None) -> bool:
    if baseline is None:
        return error_rate >= NO_BASELINE_RATE
    return exceeds(error_rate, baseline, BASELINE_MULTIPLE, BASELINE_FLOOR)


def _finding(
    window: Window,
    environment: str | None,
    model: str | None,
    calls: list[LlmSpanRow],
    errors: list[LlmSpanRow],
    error_rate: Decimal,
    baseline: Decimal | None,
) -> Finding:
    rate = fmt.round_share(error_rate)
    baseline_rate = None if baseline is None else fmt.round_share(baseline)
    critical = baseline is not None and error_rate >= CRITICAL_RATE
    params = {
        "error_rate": fmt.rate(rate),
        "model": fmt.text(model),
        "environment": fmt.text(environment),
        "errors": fmt.count(len(errors)),
        "calls": fmt.count(len(calls)),
    }
    # Absent, the catalogue swaps in its no-baseline title and summary.
    if baseline_rate is not None:
        params["baseline_error_rate"] = fmt.rate(baseline_rate)
    return Finding(
        kind=KIND,
        severity=Severity.CRITICAL if critical else Severity.WARNING,
        fingerprint_key=f"{key_part(environment)}:{key_part(model)}",
        evidence=Evidence(
            trace_ids=span_trace_ids(errors),
            metrics={
                "error_rate": rate,
                "baseline_error_rate": baseline_rate,
                "calls": len(calls),
                "errors": len(errors),
            },
            window=window,
        ),
        params=params,
    )


detector = ErrorSpikeDetector()
