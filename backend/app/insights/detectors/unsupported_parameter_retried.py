"""Doctor detector: the client drops a rejected parameter and resends.

Reads: llm spans of the last hour, fault-injected ones included (it judges client behaviour).
Rule: a span with `spanlight.fault.scenario == "unsupported_parameter"` (F), followed within 60
seconds in the same trace by a span with a different `request_hash`, whatever its status.
`rounds` counts such F. One finding per environment, warning.
Guards: needs one round; F must carry a request hash; follow-ups need a known hash. A 400 with
no follow-up is the correct behaviour and opens nothing.
Evidence: the traces of the rounds, most recent first.
"""

from datetime import timedelta

from app.insights.context import DetectorContext
from app.insights.detectors._stream_common import (
    fault_rounds,
    findings_per_environment,
)
from app.insights.schemas import Finding, Severity

KIND = "unsupported_parameter_retried"
WINDOW = timedelta(hours=1)
MIN_ROUNDS = 1
FAULT_SCENARIO = "unsupported_parameter"
FOLLOW_UP_WITHIN = timedelta(seconds=60)


class UnsupportedParameterRetried:
    kind = KIND
    window = WINDOW
    min_samples = MIN_ROUNDS

    def run(self, ctx: DetectorContext) -> list[Finding]:
        rounds = fault_rounds(ctx.llm_spans, FAULT_SCENARIO, FOLLOW_UP_WITHIN)
        if len(rounds) < MIN_ROUNDS:
            return []
        return findings_per_environment(ctx, KIND, Severity.WARNING, rounds)


detector = UnsupportedParameterRetried()
