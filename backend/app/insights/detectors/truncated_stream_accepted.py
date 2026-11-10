"""Doctor detector: a cut-off stream was treated as a complete answer.

Reads: llm spans of the last hour, fault-injected ones included (it judges client behaviour).
Rule: a span with `spanlight.fault.scenario == "truncated_stream"` (F), followed within 5 minutes
in the same trace by a span with a different `request_hash`, and by no later span in that trace
with F's `request_hash`: the client carried on instead of retrying the request or failing.
`rounds` counts such F. One finding per environment, critical.
Guards: needs one round; F must carry a request hash; follow-ups need a known hash.
Evidence: the traces of the rounds, most recent first.
"""

from datetime import timedelta

from app.insights.context import DetectorContext
from app.insights.detectors._stream_common import (
    fault_rounds,
    findings_per_environment,
)
from app.insights.schemas import Finding, Severity

KIND = "truncated_stream_accepted"
WINDOW = timedelta(hours=1)
MIN_ROUNDS = 1
FAULT_SCENARIO = "truncated_stream"
FOLLOW_UP_WITHIN = timedelta(minutes=5)


class TruncatedStreamAccepted:
    kind = KIND
    window = WINDOW
    min_samples = MIN_ROUNDS

    def run(self, ctx: DetectorContext) -> list[Finding]:
        rounds = fault_rounds(ctx.llm_spans, FAULT_SCENARIO, FOLLOW_UP_WITHIN, forbid_resend=True)
        if len(rounds) < MIN_ROUNDS:
            return []
        return findings_per_environment(ctx, KIND, Severity.CRITICAL, rounds)


detector = TruncatedStreamAccepted()
