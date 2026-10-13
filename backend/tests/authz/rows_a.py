"""The matrix rows for the gateway, alerts, budgets, insights, releases, users and exports routes.

One row per route, with the permission that is meant to guard it. `ROWS_ORG_ADMIN` adds the
Milestone-1 organization and project management routes that `test_authz.py` does not list.
"""

from app.core.permissions import Permission as P  # noqa: N817 - short, because every row names one
from tests.authz.matrix import Row

ORG = "/api/v1/orgs/{org_id}"
PRJ = "/api/v1/projects/{project_id}"

GATEWAY_ROWS = (
    Row("GET", f"{PRJ}/gateway/overview", P.PROJECT_READ),
    Row("POST", f"{PRJ}/gateway/cache/purge", P.GATEWAY_WRITE),
    # Routes and their versions.
    Row("GET", f"{PRJ}/gateway/routes", P.PROJECT_READ),
    Row("POST", f"{PRJ}/gateway/routes", P.GATEWAY_WRITE),
    Row("GET", f"{PRJ}/gateway/routes/{{route_id}}", P.PROJECT_READ),
    Row("PUT", f"{PRJ}/gateway/routes/{{route_id}}", P.GATEWAY_WRITE),
    Row("DELETE", f"{PRJ}/gateway/routes/{{route_id}}", P.GATEWAY_WRITE),
    Row("GET", f"{PRJ}/gateway/routes/{{route_id}}/versions", P.PROJECT_READ),
    Row("POST", f"{PRJ}/gateway/routes/{{route_id}}/revert", P.GATEWAY_WRITE),
    Row("POST", f"{PRJ}/gateway/routes/{{route_id}}/default", P.GATEWAY_WRITE),
    # Gateway keys.
    Row("GET", f"{PRJ}/gateway/keys", P.PROJECT_READ),
    Row("POST", f"{PRJ}/gateway/keys", P.GATEWAY_WRITE),
    Row("PATCH", f"{PRJ}/gateway/keys/{{key_id}}", P.GATEWAY_WRITE),
    Row("DELETE", f"{PRJ}/gateway/keys/{{key_id}}", P.GATEWAY_WRITE),
    # Lab fault profiles.
    Row("GET", f"{PRJ}/gateway/fault-profiles", P.PROJECT_READ),
    Row("POST", f"{PRJ}/gateway/fault-profiles", P.GATEWAY_WRITE),
    Row("PATCH", f"{PRJ}/gateway/fault-profiles/{{profile_id}}", P.GATEWAY_WRITE),
    Row("DELETE", f"{PRJ}/gateway/fault-profiles/{{profile_id}}", P.GATEWAY_WRITE),
    # Provider credentials: organization-wide, owners only to change.
    Row("GET", f"{ORG}/credentials", P.ORG_READ),
    Row("POST", f"{ORG}/credentials", P.CREDENTIALS_MANAGE),
    Row("POST", f"{ORG}/credentials/{{credential_id}}/rotate", P.CREDENTIALS_MANAGE),
    # 409 `NOT_CONFIGURED`: this app has no `CREDENTIALS_KEYS`, and the handler says
    # so before it looks the credential up (the check would call the provider).
    Row(
        "POST",
        f"{ORG}/credentials/{{credential_id}}/check",
        P.CREDENTIALS_MANAGE,
        also=frozenset({409}),
    ),
    Row("DELETE", f"{ORG}/credentials/{{credential_id}}", P.CREDENTIALS_MANAGE),
    # Price overrides: organization-wide.
    Row("GET", f"{ORG}/price-overrides", P.ORG_READ),
    Row("POST", f"{ORG}/price-overrides", P.GATEWAY_WRITE),
    Row("DELETE", f"{ORG}/price-overrides/{{override_id}}", P.GATEWAY_WRITE),
)

ALERT_ROWS = (
    # Channels and their deliveries: organization-wide.
    Row("GET", f"{ORG}/alert-channels", P.ORG_READ),
    Row("POST", f"{ORG}/alert-channels", P.ALERTS_WRITE),
    Row("GET", f"{ORG}/alert-channels/{{channel_id}}", P.ORG_READ),
    Row("PATCH", f"{ORG}/alert-channels/{{channel_id}}", P.ALERTS_WRITE),
    Row("DELETE", f"{ORG}/alert-channels/{{channel_id}}", P.ALERTS_WRITE),
    Row("POST", f"{ORG}/alert-channels/{{channel_id}}/test", P.ALERTS_WRITE),
    Row("GET", f"{ORG}/alert-channels/{{channel_id}}/deliveries", P.ORG_READ),
    Row(
        "POST",
        f"{ORG}/alert-channels/{{channel_id}}/deliveries/{{delivery_id}}/retry",
        P.ALERTS_WRITE,
    ),
    # Rules, preview, mute.
    Row("GET", f"{PRJ}/alert-rules", P.PROJECT_READ),
    Row("POST", f"{PRJ}/alert-rules", P.ALERTS_WRITE),
    # Reads only: any member of the project may draw a preview.
    Row("POST", f"{PRJ}/alert-rules/preview", P.PROJECT_READ),
    Row("GET", f"{PRJ}/alert-rules/{{rule_id}}", P.PROJECT_READ),
    Row("PATCH", f"{PRJ}/alert-rules/{{rule_id}}", P.ALERTS_WRITE),
    Row("POST", f"{PRJ}/alert-rules/{{rule_id}}/mute", P.ALERTS_WRITE),
    Row("DELETE", f"{PRJ}/alert-rules/{{rule_id}}", P.ALERTS_WRITE),
    # Events and acknowledging them.
    Row("GET", f"{PRJ}/alert-events", P.PROJECT_READ),
    Row("POST", f"{PRJ}/alert-events/{{event_id}}/acknowledge", P.ALERTS_WRITE),
    # Budgets.
    Row("GET", f"{PRJ}/budgets", P.PROJECT_READ),
    Row("POST", f"{PRJ}/budgets", P.ALERTS_WRITE),
    Row("GET", f"{PRJ}/budgets/{{budget_id}}", P.PROJECT_READ),
    Row("PATCH", f"{PRJ}/budgets/{{budget_id}}", P.ALERTS_WRITE),
    Row("DELETE", f"{PRJ}/budgets/{{budget_id}}", P.ALERTS_WRITE),
    # The weekly digest toggle is `weekly_digest_enabled` on the project (`PATCH {PRJ}`), which
    # `test_authz.py` already pins to `project:write`.
)

INSIGHT_ROWS = (
    Row("GET", f"{PRJ}/insights", P.PROJECT_READ),
    Row("GET", f"{PRJ}/insights/summary", P.PROJECT_READ),
    Row("GET", f"{PRJ}/insights/{{insight_id}}", P.PROJECT_READ),
    Row("POST", f"{PRJ}/insights/{{insight_id}}/acknowledge", P.INSIGHTS_MANAGE),
    Row("POST", f"{PRJ}/insights/{{insight_id}}/resolve", P.INSIGHTS_MANAGE),
    Row("POST", f"{PRJ}/insights/{{insight_id}}/mute", P.INSIGHTS_MANAGE),
    Row("POST", f"{PRJ}/insights/{{insight_id}}/unmute", P.INSIGHTS_MANAGE),
    Row("POST", f"{PRJ}/insights/{{insight_id}}/explain", P.INSIGHTS_MANAGE),
    Row("GET", f"{PRJ}/detector-runs", P.PROJECT_READ),
    Row("GET", f"{PRJ}/health", P.PROJECT_READ),
)

PROJECT_DATA_ROWS = (
    Row("GET", f"{PRJ}/releases", P.PROJECT_READ),
    Row("GET", f"{PRJ}/releases/compare", P.PROJECT_READ),
    Row("GET", f"{PRJ}/users", P.PROJECT_READ),
    Row("GET", f"{PRJ}/users/{{external_user_id:path}}", P.PROJECT_READ),
    Row("GET", f"{PRJ}/unpriced-models", P.PROJECT_READ),
    Row("POST", f"{PRJ}/exports", P.EXPORT_CREATE),
    Row("GET", f"{PRJ}/exports", P.PROJECT_READ),
    Row("GET", f"{PRJ}/exports/{{export_id}}", P.PROJECT_READ),
)

# Milestone-1 management routes that `test_authz.py` does not list. Removing a member needs only
# `org:read` to reach the route: the handler lets anyone remove themselves and asks for
# `member:manage` for anybody else, and the matrix always names somebody else.
ORG_ADMIN_ROWS = (
    Row("DELETE", ORG, P.ORG_DELETE),
    Row("PATCH", f"{ORG}/members/{{user_id}}", P.MEMBER_MANAGE),
    Row("DELETE", f"{ORG}/members/{{user_id}}", P.ORG_READ, then=P.MEMBER_MANAGE),
    Row("DELETE", f"{ORG}/invites/{{invite_id}}", P.MEMBER_MANAGE),
    Row("GET", f"{ORG}/audit/export.csv", P.AUDIT_READ),
    Row("DELETE", PRJ, P.PROJECT_DELETE),
    Row("DELETE", f"{PRJ}/keys/{{key_id}}", P.KEY_REVOKE_OWN),
)

ROWS_A = GATEWAY_ROWS + ALERT_ROWS + INSIGHT_ROWS + PROJECT_DATA_ROWS + ORG_ADMIN_ROWS
