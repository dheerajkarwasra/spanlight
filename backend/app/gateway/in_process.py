"""Gateway contexts for callers inside Spanlight, which have no gateway key.

Explanations, judges, the playground and experiments call `execute` with `key=None`: no rate
limits, cache or faults apply, and the caller names the trace's environment. Two shapes exist:

- `single_credential_context` builds a route in memory with one target on one credential, one
  attempt and no fallbacks. Callers that pay under a reservation use it: the reservation covers
  exactly one call, and a retry after a dropped connection could be billed twice.
- `context_for_project` loads one of the project's stored routes. Nothing calls it yet: the demo
  job uses its own gateway key (`app.services.demo_gateway`), so its calls are attributed to that
  key. It is kept as the contract for callers that want a project's routing.

The project's `block` budgets apply to both, since every call is spend in the project.
"""

import random
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GatewayRoute, Project, ProviderCredential
from app.db.rls import bind_project
from app.gateway.context import GatewayContext, GatewayServices, SpanSink, new_rng
from app.gateway.route_config import FallbackPolicy, RetryPolicy, RouteConfig, Target, load_stored
from app.gateway.route_errors import RouteNotFoundError

# An in-memory route has no row. Its id only feeds the cache key, and a call without a key is
# never cached, so a fixed value is enough.
IN_MEMORY_ROUTE_ID = uuid.UUID(int=0)
IN_MEMORY_ROUTE_VERSION = 1


def single_credential_context(
    services: GatewayServices,
    project: Project,
    credential: ProviderCredential,
    *,
    environment: str,
    route_name: str,
    span_sink: SpanSink,
    timeout_ms: int | None = None,
) -> GatewayContext:
    """The context of one in-process call in `project`, sent with `credential` and nothing else.

    Pure: builds values only. One target, one attempt, no fallbacks; `environment` labels the
    trace and `route_name` names the in-memory route on its spans. `timeout_ms` bounds the call
    (the route's default when None); a caller inside a worker task passes one shorter than the
    task's timeout, so the gateway ends a slow call itself and records it.
    """
    route = RouteConfig(
        targets=[Target(credential_id=credential.id)],
        retry=RetryPolicy(max_attempts=1),
        fallback=FallbackPolicy(on=[]),
        **({} if timeout_ms is None else {"timeout_ms": timeout_ms}),
    )
    return GatewayContext(
        key=None,
        project_id=project.id,
        org_id=project.org_id,
        environment=environment,
        route=route,
        route_id=IN_MEMORY_ROUTE_ID,
        route_name=route_name,
        route_version=IN_MEMORY_ROUTE_VERSION,
        credentials={credential.id: credential},
        capture_payloads=project.capture_payloads,
        budget_guard=services.budget_guard,
        cache=services.cache,
        http=services.http,
        sessions=services.sessions,
        settings=services.settings,
        rng=new_rng(),
        span_sink=span_sink,
    )


async def context_for_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    route_id: uuid.UUID | None,
    environment: str,
    services: GatewayServices,
    rng: random.Random | None = None,
) -> GatewayContext:
    """The context of an in-process gateway call (`key=None`) through one of the project's routes.

    For callers inside Spanlight (the demo job, explanations, the playground): no rate limits,
    cache or faults apply, and `environment` labels the trace. `route_id` None means the
    project's default route. Binds the project on `db`; reads only. Raises `RouteNotFoundError`
    when the project, the route or (for None) a default route is missing.
    """
    await bind_project(db, project_id)
    project = await db.scalar(select(Project).where(Project.id == project_id))
    if project is None:
        raise RouteNotFoundError
    which = GatewayRoute.is_default.is_(True) if route_id is None else GatewayRoute.id == route_id
    route = await db.scalar(
        select(GatewayRoute).where(GatewayRoute.project_id == project_id, which)
    )
    if route is None:
        raise RouteNotFoundError
    config = load_stored(route.config)
    credentials = await db.scalars(
        select(ProviderCredential).where(
            ProviderCredential.org_id == project.org_id,
            ProviderCredential.id.in_(config.credential_ids()),
        )
    )
    return GatewayContext(
        key=None,
        project_id=project.id,
        org_id=project.org_id,
        environment=environment,
        route=config,
        route_id=route.id,
        route_name=route.name,
        route_version=route.version,
        credentials={credential.id: credential for credential in credentials},
        capture_payloads=project.capture_payloads,
        budget_guard=services.budget_guard,
        cache=services.cache,
        http=services.http,
        sessions=services.sessions,
        settings=services.settings,
        rng=rng if rng is not None else new_rng(),
    )
