"""The matrix rows for the provider path: provider health, provider incidents, telemetry
completeness and the Doctor's feedback.

One row per route, with the permission that is meant to guard it. Every route but the feedback
vote is a read for any member; voting needs `insights:feedback` (members and up). None lets an
API key in (`tests/key_readable.py` does not list them).
"""

from app.core.permissions import Permission as P  # noqa: N817 - short, because every row names one
from tests.authz.matrix import Row

PRJ = "/api/v1/projects/{project_id}"

PROVIDER_ROWS = (
    Row("GET", f"{PRJ}/providers/health", P.PROJECT_READ),
    Row("GET", f"{PRJ}/providers/errors/timeseries", P.PROJECT_READ),
    Row("GET", f"{PRJ}/providers/errors/samples", P.PROJECT_READ),
    Row("GET", f"{PRJ}/provider-incidents", P.PROJECT_READ),
    Row("GET", f"{PRJ}/telemetry-completeness", P.PROJECT_READ),
)

FEEDBACK_ROWS = (
    Row("PUT", f"{PRJ}/insights/{{insight_id}}/feedback", P.INSIGHTS_FEEDBACK),
    Row("DELETE", f"{PRJ}/insights/{{insight_id}}/feedback", P.INSIGHTS_FEEDBACK),
    Row("GET", f"{PRJ}/insights/precision", P.PROJECT_READ),
)

ROWS_C: tuple[Row, ...] = PROVIDER_ROWS + FEEDBACK_ROWS
