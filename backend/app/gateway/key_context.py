"""Everything the gateway needs to serve a call made with a key, read before any check runs.

Call order, all in the request's transaction:

1. `parse_key(presented, GATEWAY_KEY_PREFIX)`; a `None` is `UNAUTHORIZED`.
2. `find_key(db, parsed.prefix)`; a `None` (unknown or revoked) is `UNAUTHORIZED`.
3. `key_secret_matches(parsed.secret, key.secret_hash)`; a mismatch is `UNAUTHORIZED`.
4. `load_key_context(db, key)`; a `None` (the route or project went away) is `UNAUTHORIZED`.

So nothing about the key's project is read, and no project is bound, until the presented
secret has been checked.

The key is found by its prefix before its project is known. `gateway_keys` is under row-level
security and request code never sets the bypass flag, so `find_key` goes through the read-only
policy that migration 0202 adds: with the transaction-local setting `app.gateway_key_prefix`
set to the presented prefix, exactly that one row is visible. The setting is cleared as soon as
the row is read. `load_key_context` then binds the key's project for the rest of the
transaction, so everything after it sees that project's rows and nothing else.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import Text, and_, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import GATEWAY_KEY_PREFIX
from app.db.models import FaultProfile, GatewayKey, GatewayRoute, Project, ProviderCredential
from app.db.rls import bind_project
from app.gateway.route_config import RouteConfig, load_stored

_SET_PREFIX = text("SELECT set_config('app.gateway_key_prefix', :prefix, true)")
# A stored prefix as `parse_key` produces it: the kind prefix and a 12 character lowercase
# base32 id (`app.core.security`).
_PREFIX_FORMAT = re.compile(re.escape(GATEWAY_KEY_PREFIX) + "[a-z2-7]{12}")


@dataclass(frozen=True)
class KeyContext:
    """An active key with its project, its route's current config and that config's credentials.

    `credentials` holds the sealed rows by id, never a clear provider key; a credential the
    config names that is no longer there is simply absent. `route_version` is the version the
    config was read at, for the span.
    """

    key: GatewayKey
    project_id: uuid.UUID
    org_id: uuid.UUID
    capture_payloads: bool
    route_id: uuid.UUID
    route_name: str
    route_version: int
    route: RouteConfig
    credentials: dict[uuid.UUID, ProviderCredential]
    # The profile the key runs, loaded whether or not it can fire: it may be disabled or past its
    # `expires_at`, and `decide_fault` is what looks.
    fault_profile: FaultProfile | None = None


async def find_key(db: AsyncSession, prefix: str) -> GatewayKey | None:
    """The active key with this stored prefix; None if unknown, revoked or not a prefix at all.

    `prefix` must be `parse_key(presented, GATEWAY_KEY_PREFIX).prefix` (`spl_gw_<12 base32>`).
    Anything else is refused before it reaches the database, so a value that Postgres would
    reject as a setting (a NUL byte, for one) is an unknown key, not an error. One query, under
    the prefix policy; no project is bound.
    """
    if not _PREFIX_FORMAT.fullmatch(prefix):
        return None
    await db.execute(_SET_PREFIX, {"prefix": prefix})
    key = await db.scalar(select(GatewayKey).where(GatewayKey.prefix == prefix))
    await db.execute(_SET_PREFIX, {"prefix": ""})
    if key is None or key.revoked_at is not None or key.route_id is None:
        return None
    return key


async def load_key_context(db: AsyncSession, key: GatewayKey) -> KeyContext | None:
    """The context of a key `find_key` returned and whose secret the caller has checked.

    One query with the key's project bound: the project, the route, the route's credentials and
    the key's fault profile. None if the project or the route went away since `find_key`.
    Leaves the key's project bound. The route config is read back with `load_stored`, so a rule
    tightened after it was saved never fails a call.
    """
    if key.route_id is None:
        return None
    await bind_project(db, key.project_id)
    targeted = GatewayRoute.config["targets"].contains(
        func.jsonb_build_array(
            func.jsonb_build_object("credential_id", cast(ProviderCredential.id, Text))
        )
    )
    rows = await db.execute(
        select(Project, GatewayRoute, ProviderCredential, FaultProfile)
        .join(
            GatewayRoute,
            and_(GatewayRoute.project_id == Project.id, GatewayRoute.id == key.route_id),
        )
        .outerjoin(
            FaultProfile,
            and_(
                FaultProfile.project_id == Project.id,
                FaultProfile.id == key.fault_profile_id,
            ),
        )
        .outerjoin(
            ProviderCredential,
            and_(ProviderCredential.org_id == Project.org_id, targeted),
        )
        .where(Project.id == key.project_id)
    )
    found = list(rows)
    if not found:
        # The project or the route went away since `find_key`.
        return None
    project, route, _, fault_profile = found[0]
    return KeyContext(
        key=key,
        project_id=project.id,
        org_id=project.org_id,
        capture_payloads=project.capture_payloads,
        route_id=route.id,
        route_name=route.name,
        route_version=route.version,
        route=load_stored(route.config),
        credentials={
            credential.id: credential for _, _, credential, _ in found if credential is not None
        },
        fault_profile=fault_profile,
    )
