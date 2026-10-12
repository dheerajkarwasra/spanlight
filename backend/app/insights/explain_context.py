"""The gateway context of an explanation: one credential, a route built in memory, no key.

An explanation is not a call through one of the project's routes (the organization's oldest
Anthropic credential may serve no route at all), so the route is built in memory by
`app.gateway.in_process.single_credential_context`: a single target on that credential, one
attempt and no fallbacks (the budget reserves the cost of exactly one call, and a retry after a
dropped connection could be billed twice). `key=None` means no cache, no faults and no key limits;
the project's `block` budgets still apply, since the call is spend in the project.
"""

from app.db.models import Project, ProviderCredential
from app.gateway.context import GatewayContext, GatewayServices, SpanSink
from app.gateway.in_process import single_credential_context

ENVIRONMENT = "doctor"
ROUTE_NAME = "doctor-explain"


def explain_context(
    services: GatewayServices,
    project: Project,
    credential: ProviderCredential,
    *,
    span_sink: SpanSink,
) -> GatewayContext:
    """The context of one explanation call in `project`, sent with `credential`."""
    return single_credential_context(
        services,
        project,
        credential,
        environment=ENVIRONMENT,
        route_name=ROUTE_NAME,
        span_sink=span_sink,
    )
