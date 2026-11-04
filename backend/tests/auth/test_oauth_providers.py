"""Turning what GitHub and Google answer into an `OAuthProfile`, and the OAuth settings."""

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.auth.oauth_client import ProviderError, fetch_profile
from app.auth.oauth_providers import (
    PROVIDERS,
    OAuthProfile,
    parse_github_profile,
    parse_google_profile,
)
from app.config import Settings


def _github_user(**overrides: Any) -> dict[str, Any]:
    return {"id": 1001, "login": "ada-gh", "name": "Ada Lovelace", **overrides}


def _email(address: str, *, primary: bool = True, verified: bool = True) -> dict[str, Any]:
    return {"email": address, "primary": primary, "verified": verified, "visibility": None}


# --- GitHub --------------------------------------------------------------------------------------


def test_github_profile_uses_the_primary_email_and_its_verified_flag() -> None:
    profile = parse_github_profile(
        _github_user(),
        [_email("other@example.com", primary=False), _email("ada@example.com")],
    )

    assert profile == OAuthProfile(
        provider="github",
        subject="1001",
        email="ada@example.com",
        email_verified=True,
        name="Ada Lovelace",
    )


def test_github_primary_email_unverified_is_unverified() -> None:
    profile = parse_github_profile(
        _github_user(),
        [_email("verified@example.com", primary=False), _email("ada@example.com", verified=False)],
    )

    assert (profile.email, profile.email_verified) == ("ada@example.com", False)


@pytest.mark.parametrize("emails", [[], [_email("a@example.com", primary=False)]])
def test_github_without_a_primary_email_has_none(emails: list[dict[str, Any]]) -> None:
    profile = parse_github_profile(_github_user(), emails)

    assert (profile.email, profile.email_verified) == (None, False)


def test_github_name_falls_back_to_the_login() -> None:
    assert parse_github_profile(_github_user(name=None), []).name == "ada-gh"
    assert parse_github_profile(_github_user(name="  "), []).name == "ada-gh"


def test_github_verified_must_be_the_boolean_true() -> None:
    for value in ("true", 1, "yes", None):
        email = {**_email("ada@example.com"), "verified": value}
        assert parse_github_profile(_github_user(), [email]).email_verified is False


@pytest.mark.parametrize(
    "user",
    [{}, {"login": "x"}, {"id": None}, {"id": True}, {"id": "1001"}, {"id": 1.5}, [], "x", None],
)
def test_github_user_without_a_numeric_id_is_malformed(user: Any) -> None:
    with pytest.raises(ValueError, match="id"):
        parse_github_profile(user, [])


@pytest.mark.parametrize("emails", [{}, "x", None, 3, [1], ["a@example.com"]])
def test_github_emails_that_are_not_a_list_of_objects_are_malformed(emails: Any) -> None:
    with pytest.raises(ValueError, match="emails"):
        parse_github_profile(_github_user(), emails)


def test_github_ignores_an_email_that_is_not_an_address() -> None:
    profile = parse_github_profile(_github_user(), [_email("not-an-address")])

    assert (profile.email, profile.email_verified) == (None, False)


# --- Google --------------------------------------------------------------------------------------


def test_google_profile() -> None:
    profile = parse_google_profile(
        {"sub": "g-1", "email": "ada@example.com", "email_verified": True, "name": "Ada"}
    )

    assert profile == OAuthProfile(
        provider="google",
        subject="g-1",
        email="ada@example.com",
        email_verified=True,
        name="Ada",
    )


@pytest.mark.parametrize(("raw", "expected"), [(True, True), ("true", True), (False, False)])
def test_google_email_verified_accepts_the_two_forms_google_uses(raw: Any, expected: bool) -> None:
    info = {"sub": "g", "email": "a@example.com", "email_verified": raw}

    assert parse_google_profile(info).email_verified is expected


@pytest.mark.parametrize("raw", ["True", "false", "yes", 1, 0, None, [], {}])
def test_google_email_verified_is_false_for_anything_else(raw: Any) -> None:
    info = {"sub": "g", "email": "a@example.com", "email_verified": raw}

    assert parse_google_profile(info).email_verified is False


def test_google_without_an_email_is_unverified() -> None:
    profile = parse_google_profile({"sub": "g", "email_verified": True})

    assert (profile.email, profile.email_verified) == (None, False)


def test_google_name_is_optional() -> None:
    assert parse_google_profile({"sub": "g", "email": "a@example.com"}).name is None


@pytest.mark.parametrize("info", [{}, {"sub": ""}, {"sub": 5}, {"sub": None}, [], "x", None])
def test_google_without_a_subject_is_malformed(info: Any) -> None:
    with pytest.raises(ValueError, match="sub"):
        parse_google_profile(info)


# --- the registry --------------------------------------------------------------------------------


def test_the_registry_has_the_documented_endpoints_and_scopes() -> None:
    github, google = PROVIDERS["github"], PROVIDERS["google"]

    assert github.authorize_url == "https://github.com/login/oauth/authorize"
    assert github.token_url == "https://github.com/login/oauth/access_token"
    assert github.scope == "read:user user:email"
    assert google.authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert google.token_url == "https://oauth2.googleapis.com/token"
    assert google.scope == "openid email profile"
    assert all(spec.authorize_url.startswith("https://") for spec in PROVIDERS.values())


# --- settings ------------------------------------------------------------------------------------


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)


def test_no_oauth_settings_means_no_providers() -> None:
    assert _settings().oauth_providers == ()


def test_a_provider_is_enabled_when_both_values_are_set() -> None:
    settings = _settings(
        oauth_github_client_id="id",
        oauth_github_client_secret="secret",
    )

    assert settings.oauth_providers == ("github",)


def test_both_providers_are_listed_in_a_fixed_order() -> None:
    settings = _settings(
        oauth_google_client_id="g",
        oauth_google_client_secret="gs",
        oauth_github_client_id="h",
        oauth_github_client_secret="hs",
    )

    assert settings.oauth_providers == ("github", "google")


def test_blank_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB", "GOOGLE"):
        monkeypatch.setenv(f"OAUTH_{name}_CLIENT_ID", "")
        monkeypatch.setenv(f"OAUTH_{name}_CLIENT_SECRET", "")

    assert _settings().oauth_providers == ()


def test_the_client_secret_never_shows_in_the_settings_text() -> None:
    settings = _settings(
        oauth_github_client_id="id",
        oauth_github_client_secret="super-secret-value",
    )

    assert "super-secret-value" not in repr(settings)


@pytest.mark.parametrize(
    "values",
    [
        {"oauth_github_client_id": "id"},
        {"oauth_github_client_secret": "do-not-print-this"},
        {"oauth_google_client_id": "id"},
        {"oauth_google_client_secret": "do-not-print-this"},
    ],
)
def test_half_a_provider_fails_at_startup_without_printing_values(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError) as error:
        _settings(**values)

    message = str(error.value)
    assert "CLIENT_ID and OAUTH_" in message
    assert "do-not-print-this" not in message


# --- the token client ----------------------------------------------------------------------------


async def test_the_token_client_ignores_proxy_and_netrc_settings_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # HTTP(S)_PROXY or ~/.netrc on the host must not see the client secret or the code.
    trust_env: list[bool] = []

    class RecordingClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            trust_env.append(kwargs.get("trust_env", True))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", RecordingClient)
    settings = _settings(oauth_github_client_id="id", oauth_github_client_secret="secret")
    refuse = httpx.MockTransport(lambda request: httpx.Response(500))

    with pytest.raises(ProviderError):
        await fetch_profile(settings, "github", code="c", verifier="v", transport=refuse)

    assert trust_env == [False]
