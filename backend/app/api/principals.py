"""Who is making a request, once it has been authenticated.

A request has at most one principal. A credential is never combined with another: a request
that carries an `Authorization: Bearer` header is authenticated by that header alone and its
cookies are ignored (see `app.core.middleware.BearerRequestMiddleware`).
"""

import uuid
from dataclasses import dataclass

from app.core.scopes import KeyScope
from app.db.models import Session, TokenScope, User


@dataclass(frozen=True)
class SessionPrincipal:
    """A signed-in browser: the session cookie (`__Host-spl_session` over https, `spl_session`
    over http). May use every dashboard route."""

    user: User
    session: Session


@dataclass(frozen=True)
class TokenPrincipal:
    """A personal access token (`Authorization: Bearer spl_pat_…`): a user, acting through a script.

    It has the user's identity and no permissions of its own. Routes guarded by `require()` look
    up the user's memberships on every request, and a `read` token is further limited to the
    permissions classed `read`. Routes that are for a browser session (account, sessions, tokens,
    sign-in) refuse it, so a leaked token cannot be turned into a session or into more tokens.
    """

    user: User
    token_id: uuid.UUID
    scope: TokenScope


@dataclass(frozen=True)
class ApiKeyPrincipal:
    """A project API key (`Authorization: Bearer spl_live_…`): one project, a fixed set of scopes.

    It has no user and no organization role, so it never passes a membership check; the routes
    that accept it check its project and scopes instead.
    """

    key_id: uuid.UUID
    project_id: uuid.UUID
    scopes: frozenset[KeyScope]


UserPrincipal = SessionPrincipal | TokenPrincipal
"""A principal that is a person's account: what a membership check needs."""

Principal = SessionPrincipal | TokenPrincipal | ApiKeyPrincipal
