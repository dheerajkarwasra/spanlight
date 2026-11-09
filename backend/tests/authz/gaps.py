"""Routes that are in no authorization matrix on purpose.

`tests/authz/test_matrix_completeness.py` fails for a route that is in `milestone_1.py`, in the
matrix rows or here, and for an entry here that is stale.
"""

from tests.authz.matrix import Route

# Needs a session, or no sign-in at all, and names no organization or project: there is no role
# to vary. Which credential each of them accepts is pinned by the route inventories in
# `tests/keys/` (`test_token_route_inventory.py`, `test_anonymous_routes.py`).
_ACCOUNT = "no organization or project in the path; only the caller's own account"
_SIGN_IN = "reachable before sign-in or part of signing in: there is no role yet"
_INVITE_TOKEN = "authorized by the invite token in the request, not by a membership"
_PUBLIC = "starts an anonymous demo session: no sign-in to check"
_UNSUBSCRIBE_TOKEN = "anonymous; authorized by the emailed unsubscribe token, not by a membership"

NOT_IN_MATRIX: dict[Route, str] = {
    ("POST", "/api/v1/auth/signup"): _SIGN_IN,
    ("POST", "/api/v1/auth/login"): _SIGN_IN,
    ("POST", "/api/v1/auth/logout"): _ACCOUNT,
    ("GET", "/api/v1/auth/me"): _ACCOUNT,
    ("PATCH", "/api/v1/auth/me"): _ACCOUNT,
    ("DELETE", "/api/v1/auth/me"): _ACCOUNT,
    ("PUT", "/api/v1/auth/me/activation-checklist"): _ACCOUNT,
    ("GET", "/api/v1/auth/terms"): _SIGN_IN,
    ("POST", "/api/v1/auth/terms/accept"): _ACCOUNT,
    ("GET", "/api/v1/auth/sessions"): _ACCOUNT,
    ("DELETE", "/api/v1/auth/sessions"): _ACCOUNT,
    ("DELETE", "/api/v1/auth/sessions/{session_id}"): _ACCOUNT,
    ("POST", "/api/v1/auth/email/verify/request"): _ACCOUNT,
    ("POST", "/api/v1/auth/email/verify/confirm"): _SIGN_IN,
    ("POST", "/api/v1/auth/email/change"): _ACCOUNT,
    ("POST", "/api/v1/auth/email/change/confirm"): _SIGN_IN,
    ("POST", "/api/v1/email/unsubscribe"): _UNSUBSCRIBE_TOKEN,
    ("POST", "/api/v1/auth/password/forgot"): _SIGN_IN,
    ("POST", "/api/v1/auth/password/reset"): _SIGN_IN,
    ("GET", "/api/v1/auth/totp"): _ACCOUNT,
    ("POST", "/api/v1/auth/totp/setup"): _ACCOUNT,
    ("POST", "/api/v1/auth/totp/enable"): _ACCOUNT,
    ("POST", "/api/v1/auth/totp/disable"): _ACCOUNT,
    ("POST", "/api/v1/auth/totp/verify"): _ACCOUNT,
    ("GET", "/api/v1/auth/tokens"): _ACCOUNT,
    ("POST", "/api/v1/auth/tokens"): _ACCOUNT,
    ("POST", "/api/v1/auth/oauth/{provider}/link"): _ACCOUNT,
    ("DELETE", "/api/v1/auth/tokens/{token_id}"): _ACCOUNT,
    ("GET", "/api/v1/auth/oauth/providers"): _SIGN_IN,
    ("GET", "/api/v1/auth/oauth/identities"): _ACCOUNT,
    ("DELETE", "/api/v1/auth/oauth/{provider}"): _ACCOUNT,
    ("GET", "/api/v1/auth/oauth/{provider}/start"): _SIGN_IN,
    ("GET", "/api/v1/auth/oauth/{provider}/callback"): _SIGN_IN,
    ("POST", "/api/v1/orgs"): "creates an organization of the caller's own: no membership to check",
    ("POST", "/api/v1/invites/preview"): _INVITE_TOKEN,
    ("POST", "/api/v1/invites/accept"): _INVITE_TOKEN,
    ("GET", "/api/v1/prices"): "the global price table: any signed-in user may read it",
    ("POST", "/api/v1/demo/session"): _PUBLIC,
    # Guarded by `require(project:read)`, which every role holds; a stream never ends, so the
    # matrix cannot call it. `tests/api/test_live.py` covers members, non-members and removal.
    ("GET", "/api/v1/projects/{project_id}/live"): "an open-ended event stream; see test_live.py",
}
