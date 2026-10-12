"""The single source of truth for what each membership role may do.

Roles are cumulative: each role holds every permission of the roles below it.
Route handlers never compare roles directly; they declare the permission they
need via `app.api.deps.require(...)` and, for finer decisions, call `has()`.

Each permission is also classed `read` or `write` (`permission_class`). A personal access token
with the `read` scope is limited to the `read` ones whatever its user's role allows.
"""

import enum
from typing import Literal

from app.db.models import MembershipRole


class Permission(enum.StrEnum):
    ORG_READ = "org:read"
    PROJECT_READ = "project:read"
    KEY_CREATE = "key:create"
    KEY_REVOKE_OWN = "key:revoke_own"
    KEY_REVOKE_ANY = "key:revoke_any"
    PROJECT_WRITE = "project:write"
    PROJECT_DELETE = "project:delete"
    MEMBER_MANAGE = "member:manage"
    AUDIT_READ = "audit:read"
    ORG_UPDATE = "org:update"
    ORG_DELETE = "org:delete"
    ORG_SECURITY = "org:security"
    EXPORT_CREATE = "export:create"
    GATEWAY_WRITE = "gateway:write"
    CREDENTIALS_MANAGE = "credentials:manage"
    ALERTS_WRITE = "alerts:write"
    INSIGHTS_MANAGE = "insights:manage"
    SCORES_WRITE = "scores:write"
    ANNOTATIONS_WRITE = "annotations:write"
    PROMPTS_WRITE = "prompts:write"
    DATASETS_WRITE = "datasets:write"
    EXPERIMENTS_RUN = "experiments:run"
    EVALUATORS_WRITE = "evaluators:write"


PermissionClass = Literal["read", "write"]

# Whether using a permission can change anything. A personal access token with the `read` scope
# may only use `read` permissions. A permission is listed here explicitly, with no default, so
# adding one without deciding which kind it is fails the test that compares this table with the
# enum, and fails closed (`KeyError`) until then.
PERMISSION_CLASSES: dict[Permission, PermissionClass] = {
    Permission.ORG_READ: "read",
    Permission.PROJECT_READ: "read",
    Permission.AUDIT_READ: "read",
    Permission.KEY_CREATE: "write",
    Permission.KEY_REVOKE_OWN: "write",
    Permission.KEY_REVOKE_ANY: "write",
    Permission.PROJECT_WRITE: "write",
    Permission.PROJECT_DELETE: "write",
    Permission.MEMBER_MANAGE: "write",
    Permission.ORG_UPDATE: "write",
    Permission.ORG_DELETE: "write",
    Permission.ORG_SECURITY: "write",
    Permission.EXPORT_CREATE: "write",
    Permission.GATEWAY_WRITE: "write",
    Permission.CREDENTIALS_MANAGE: "write",
    Permission.ALERTS_WRITE: "write",
    Permission.INSIGHTS_MANAGE: "write",
    Permission.SCORES_WRITE: "write",
    Permission.ANNOTATIONS_WRITE: "write",
    Permission.PROMPTS_WRITE: "write",
    Permission.DATASETS_WRITE: "write",
    Permission.EXPERIMENTS_RUN: "write",
    Permission.EVALUATORS_WRITE: "write",
}


def permission_class(permission: Permission) -> PermissionClass:
    return PERMISSION_CLASSES[permission]


_VIEWER = frozenset({Permission.ORG_READ, Permission.PROJECT_READ})
_MEMBER = _VIEWER | {
    Permission.KEY_CREATE,
    Permission.KEY_REVOKE_OWN,
    Permission.EXPORT_CREATE,
    Permission.SCORES_WRITE,
    Permission.ANNOTATIONS_WRITE,
    Permission.PROMPTS_WRITE,
    Permission.DATASETS_WRITE,
    Permission.EXPERIMENTS_RUN,
}
_ADMIN = _MEMBER | {
    Permission.KEY_REVOKE_ANY,
    Permission.PROJECT_WRITE,
    Permission.PROJECT_DELETE,
    Permission.MEMBER_MANAGE,
    Permission.ORG_UPDATE,
    Permission.AUDIT_READ,
    Permission.GATEWAY_WRITE,
    Permission.ALERTS_WRITE,
    Permission.INSIGHTS_MANAGE,
    Permission.EVALUATORS_WRITE,
}
_OWNER = _ADMIN | {Permission.ORG_DELETE, Permission.ORG_SECURITY, Permission.CREDENTIALS_MANAGE}

ROLE_PERMISSIONS: dict[MembershipRole, frozenset[Permission]] = {
    MembershipRole.VIEWER: _VIEWER,
    MembershipRole.MEMBER: frozenset(_MEMBER),
    MembershipRole.ADMIN: frozenset(_ADMIN),
    MembershipRole.OWNER: frozenset(_OWNER),
}


def has(role: MembershipRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def can_assign_role(actor_role: MembershipRole, target_role: MembershipRole) -> bool:
    """Only owners may grant (or alter) the owner role; admins manage the rest."""
    if not has(actor_role, Permission.MEMBER_MANAGE):
        return False
    return target_role is not MembershipRole.OWNER or actor_role is MembershipRole.OWNER
