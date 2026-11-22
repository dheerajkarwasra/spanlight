"""`tool_schema_hallucination`: an agent calls unknown tools or passes invalid arguments.

Reads: `ctx.tool_spans` of the last hour.
Rule: a failed tool span (`status == "error"`) whose `status_message` matches schema/validation
failures (`unknown tool`, `not found`, `invalid argument`, `validation error`, `missing required`,
`unexpected keyword`, `schema`, `type error`). Fires per tool name at >= 3 errors in the hour,
critical at >= 15 errors. Key: tool name. Metrics: `errors`, `traces`.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from app.insights import format as fmt
from app.insights.context import DetectorContext, ToolSpanRow
from app.insights.detectors._evidence import by_key, recent_trace_ids
from app.insights.schemas import Evidence, Finding, Severity

KIND = "tool_schema_hallucination"
WINDOW = timedelta(hours=1)
MIN_SAMPLES = 3

MIN_ERRORS = 3
CRITICAL_ERRORS = 15

_SCHEMA_PATTERN = re.compile(
    r"unknown tool|tool .*not found|invalid argument|validation error|"
    r"missing required|unexpected keyword|json schema|schema error|typeerror|pydantic",
    re.IGNORECASE,
)


def _is_schema_error(span: ToolSpanRow) -> bool:
    return (
        span.status == "error"
        and span.status_message is not None
        and _SCHEMA_PATTERN.search(span.status_message) is not None
    )


@dataclass(frozen=True, slots=True)
class ToolSchemaHallucinationDetector:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = MIN_SAMPLES

    def run(self, ctx: DetectorContext) -> list[Finding]:
        grouped: dict[str, list[ToolSpanRow]] = defaultdict(list)
        for span in ctx.tool_spans:
            if _is_schema_error(span):
                grouped[span.name].append(span)

        findings: list[Finding] = []
        for tool_name, failures in grouped.items():
            if len(failures) < MIN_ERRORS:
                continue
            traces = {s.trace_id for s in failures}
            severity = Severity.CRITICAL if len(failures) >= CRITICAL_ERRORS else Severity.WARNING
            findings.append(
                Finding(
                    kind=KIND,
                    severity=severity,
                    fingerprint_key=tool_name,
                    evidence=Evidence(
                        trace_ids=recent_trace_ids((s.started_at, s.trace_id) for s in failures),
                        metrics={"errors": len(failures), "traces": len(traces)},
                        window=ctx.window,
                    ),
                    params={
                        "name": tool_name,
                        "errors": fmt.count_noun(len(failures), "schema failure"),
                        "traces": fmt.count_noun(len(traces), "trace"),
                    },
                )
            )
        return by_key(findings)


detector = ToolSchemaHallucinationDetector()
