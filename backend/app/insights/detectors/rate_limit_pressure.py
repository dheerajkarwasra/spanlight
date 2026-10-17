"""rate_limit_pressure: a large share of calls to one provider get rate limited.

Reads: llm spans of the last 15 minutes. Fault-injected spans (`gateway.fault_scenario` set)
and the gateway key limiter's own refusals (`rate_limit_kind == "gateway"`) are dropped before
counting, `min_samples` included: a simulated 429 says nothing about the real provider limit,
and a call Spanlight's limiter refused never reached the provider.

Rule: per (environment, provider), the share of spans with error class `rate_limit` among all
calls. At or above `WARNING_SHARE` the finding is a warning, at or above `CRITICAL_SHARE`
critical. A group with fewer than `MIN_CALLS` calls is ignored.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.insights import format as fmt
from app.insights.context import DetectorContext, LlmSpanRow
from app.insights.detectors._evidence import by_key, key_part, span_trace_ids
from app.insights.detectors.baselines import provider_traffic
from app.insights.schemas import Evidence, Finding, Severity

KIND = "rate_limit_pressure"
WINDOW = timedelta(minutes=15)
MIN_CALLS = 50
WARNING_SHARE = Decimal("0.10")
CRITICAL_SHARE = Decimal("0.30")


def _finding(
    environment: str | None,
    provider: str | None,
    spans: list[LlmSpanRow],
    ctx: DetectorContext,
) -> Finding | None:
    limited = [s for s in spans if s.error_class == "rate_limit"]
    share = Decimal(len(limited)) / Decimal(len(spans))
    if share < WARNING_SHARE:
        return None
    return Finding(
        kind=KIND,
        severity=Severity.CRITICAL if share >= CRITICAL_SHARE else Severity.WARNING,
        fingerprint_key=f"{key_part(environment)}:{key_part(provider)}",
        evidence=Evidence(
            trace_ids=span_trace_ids(limited),
            metrics={
                "share": fmt.round_share(share),
                "rate_limited": len(limited),
                "calls": len(spans),
            },
            window=ctx.window,
        ),
        params={
            "share": fmt.rate(share),
            "provider": fmt.text(provider),
            "rate_limited": fmt.count(len(limited)),
            "calls": fmt.count(len(spans)),
            "environment": fmt.text(environment),
        },
    )


@dataclass(frozen=True, slots=True)
class RateLimitPressureDetector:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = MIN_CALLS

    def run(self, ctx: DetectorContext) -> list[Finding]:
        groups: dict[tuple[str | None, str | None], list[LlmSpanRow]] = defaultdict(list)
        for span in provider_traffic(ctx.llm_spans):
            groups[(span.environment, span.provider)].append(span)

        findings: list[Finding] = []
        for (environment, provider), spans in groups.items():
            if len(spans) >= self.min_samples:
                finding = _finding(environment, provider, spans, ctx)
                if finding is not None:
                    findings.append(finding)
        return by_key(findings)


detector = RateLimitPressureDetector()
