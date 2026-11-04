"""Talking to GitHub and Google: the authorization URL, and the code-for-profile exchange.

This is the only module that touches the provider's servers, and it uses plain `httpx`. The
authorization-code flow with PKCE is a form POST and two GETs, which does not need an OAuth
framework, and an extra HTTP stack would split the app's one `httpx` into two (the tests'
`httpx.MockTransport` would no longer plug into it). The provider access token is used for the
profile calls inside `fetch_profile` and then dropped: it is not returned, stored or logged.
Nothing here decides what a profile may do; see `app.auth.oauth_service`.
"""

import base64
import hashlib
import re
from typing import Any
from urllib.parse import urlencode

import httpx

from app.auth.oauth_providers import (
    GITHUB_EMAILS_URL,
    GITHUB_USER_URL,
    GOOGLE_USERINFO_URL,
    PROVIDERS,
    OAuthProfile,
    parse_github_profile,
    parse_google_profile,
)
from app.config import Settings

PROVIDER_TIMEOUT_SECONDS = 10.0
_ERROR_CODE = re.compile(r"[a-z_]{1,64}")


class ProviderError(Exception):
    """The provider refused the code, could not be reached, or answered with something unusable.

    The message names the kind of failure only. It never holds a response body or a token.
    """


def redirect_uri(settings: Settings, provider: str) -> str:
    """The callback URL registered with the provider (GitHub and Google compare it exactly)."""
    return f"{settings.app_base_url}/api/v1/auth/oauth/{provider}/callback"


def code_challenge(verifier: str) -> str:
    """The PKCE S256 challenge for a verifier (RFC 7636): base64url of its SHA-256, unpadded."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(settings: Settings, provider: str, *, state: str, verifier: str) -> str:
    """Where to send the browser to sign in at the provider, with a PKCE S256 challenge."""
    credentials = settings.oauth_credentials(provider)
    if credentials is None:
        raise ProviderError(f"{provider} is not configured")
    spec = PROVIDERS[provider]
    query = urlencode(
        {
            "client_id": credentials[0],
            "response_type": "code",
            "redirect_uri": redirect_uri(settings, provider),
            "scope": spec.scope,
            "state": state,
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{spec.authorize_url}?{query}"


async def fetch_profile(
    settings: Settings,
    provider: str,
    *,
    code: str,
    verifier: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OAuthProfile:
    """Exchange an authorization code for the signed-in person's profile.

    `transport` replaces the network in tests. Any failure on the way becomes a `ProviderError`.
    """
    credentials = settings.oauth_credentials(provider)
    if credentials is None:
        raise ProviderError(f"{provider} is not configured")
    client_id, client_secret = credentials
    spec = PROVIDERS[provider]
    try:
        # trust_env=False: no HTTP(S)_PROXY or .netrc from the host sees the code or the secret.
        async with httpx.AsyncClient(
            transport=transport,
            timeout=PROVIDER_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                spec.token_url,
                # `Accept` matters for GitHub, which otherwise answers in a form encoding.
                headers={"Accept": "application/json"},
                # Credentials in the form, as GitHub documents; Google accepts them there too.
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri(settings, provider),
                    "client_id": client_id,
                    "client_secret": client_secret.get_secret_value(),
                    "code_verifier": verifier,
                },
            )
            access_token = _access_token(provider, response)
            bearer = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            if provider == "github":
                user = await _get_json(client, GITHUB_USER_URL, bearer)
                emails = await _get_json(client, GITHUB_EMAILS_URL, bearer)
                return parse_github_profile(user, emails)
            return parse_google_profile(await _get_json(client, GOOGLE_USERINFO_URL, bearer))
    except (httpx.HTTPError, ValueError) as error:
        raise ProviderError(f"{provider} failed: {type(error).__name__}") from error


def _access_token(provider: str, response: httpx.Response) -> str:
    """The access token in a token response, or a `ProviderError` that says why not.

    GitHub answers a bad or reused code with status 200 and an `error` field, so the body is
    read whatever the status.
    """
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ProviderError(f"{provider} token endpoint answered {response.status_code}")
    error = body.get("error")
    if error is not None or response.is_error:
        # The error code is a short word from the provider ("bad_verification_code"). Only a
        # well-formed one is repeated; a description or anything odd is not.
        code = error if isinstance(error, str) and _ERROR_CODE.fullmatch(error) else "unknown"
        raise ProviderError(f"{provider} refused the code: {code}")
    token = body.get("access_token")
    token_type = body.get("token_type", "bearer")
    if not isinstance(token, str) or not token:
        raise ProviderError(f"{provider} token response has no access token")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise ProviderError(f"{provider} token response is not a bearer token")
    return token


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> Any:
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    return response.json()
