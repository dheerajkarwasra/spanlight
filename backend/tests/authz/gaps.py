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
_TOKEN_IN_URL = "authorized by the invite token in the request, not by a membership"
_PUBLIC = "starts an anonymous demo session: no sign-in to check"

NOT_IN_MATRIX: dict[Route, str] = {
    ("POST", "/api/v1/auth/signup"): _SIGN_IN,
    ("POST", "/api/v1/auth/login"): _SIGN_IN,
    ("POST", "/api/v1/auth/logout"): _ACCOUNT,
    ("GET", "/api/v1/auth/me"): _ACCOUNT,
    ("GET", "/api/v1/auth/sessions"): _ACCOUNT,
    ("DELETE", "/api/v1/auth/sessions"): _ACCOUNT,
    ("DELETE", "/api/v1/auth/sessions/{session_id}"): _ACCOUNT,
    ("POST", "/api/v1/auth/email/verify/request"): _ACCOUNT,
    ("POST", "/api/v1/auth/email/verify/confirm"): _SIGN_IN,
    ("POST", "/api/v1/auth/password/forgot"): _SIGN_IN,
    ("POST", "/api/v1/auth/password/reset"): _SIGN_IN,
    ("GET", "/api/v1/auth/totp"): _ACCOUNT,
    ("POST", "/api/v1/auth/totp/setup"): _ACCOUNT,
    ("POST", "/api/v1/auth/totp/enable"): _ACCOUNT,
    ("POST", "/api/v1/auth/totp/disable"): _ACCOUNT,
    ("POST", "/api/v1/auth/totp/verify"): _ACCOUNT,
    ("GET", "/api/v1/auth/tokens"): _ACCOUNT,
    ("POST", "/api/v1/auth/tokens"): _ACCOUNT,
    ("DELETE", "/api/v1/auth/tokens/{token_id}"): _ACCOUNT,
    ("GET", "/api/v1/auth/oauth/providers"): _SIGN_IN,
    ("GET", "/api/v1/auth/oauth/identities"): _ACCOUNT,
    ("DELETE", "/api/v1/auth/oauth/{provider}"): _ACCOUNT,
    ("GET", "/api/v1/auth/oauth/{provider}/start"): _SIGN_IN,
    ("GET", "/api/v1/auth/oauth/{provider}/callback"): _SIGN_IN,
    ("POST", "/api/v1/orgs"): "creates an organization of the caller's own: no membership to check",
    ("GET", "/api/v1/invites/preview"): _TOKEN_IN_URL,
    ("POST", "/api/v1/invites/accept"): _TOKEN_IN_URL,
    ("GET", "/api/v1/prices"): "the global price table: any signed-in user may read it",
    ("POST", "/api/v1/demo/session"): _PUBLIC,
}
