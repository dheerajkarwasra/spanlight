"""The signed `spl_oauth` cookie and the rule for where a sign-in may send the browser."""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Response

from app.auth.oauth_state import (
    STATE_COOKIE,
    OAuthState,
    _seal,
    clear_state_cookie,
    new_state,
    read_state,
    safe_next,
    set_state_cookie,
    sign_state,
    state_matches,
)
from app.config import Settings

SECRET = "a-secret-key-for-tests-32-characters-long"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _state(**overrides: object) -> OAuthState:
    fields: dict[str, object] = {
        "state": "state-value",
        "verifier": "v" * 64,
        "provider": "github",
        "intent": "sign_in",
        "next": "/dashboard",
        "user_id": None,
        "exp": NOW + timedelta(minutes=10),
    }
    fields.update(overrides)
    return OAuthState(**fields)  # type: ignore[arg-type]


# --- the cookie value -------------------------------------------------------------------------


def test_a_signed_state_reads_back() -> None:
    original = _state(intent="link", user_id=uuid.uuid4(), next="/settings/security")

    assert read_state(SECRET, sign_state(SECRET, original), now=NOW) == original


def test_new_state_is_random_and_valid_for_ten_minutes() -> None:
    first = new_state(provider="github", intent="sign_in", next="/", user_id=None, now=NOW)
    second = new_state(provider="github", intent="sign_in", next="/", user_id=None, now=NOW)

    assert first.state != second.state
    assert first.verifier != second.verifier
    assert first.exp == NOW + timedelta(minutes=10)
    assert len(first.state) >= 32


def test_the_pkce_verifier_follows_rfc_7636() -> None:
    state = new_state(provider="github", intent="sign_in", next="/", user_id=None, now=NOW)

    assert 43 <= len(state.verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", state.verifier)


def test_a_state_expires() -> None:
    cookie = sign_state(SECRET, _state(exp=NOW + timedelta(minutes=10)))

    assert read_state(SECRET, cookie, now=NOW + timedelta(minutes=9, seconds=59)) is not None
    assert read_state(SECRET, cookie, now=NOW + timedelta(minutes=10)) is None
    assert read_state(SECRET, cookie, now=NOW + timedelta(hours=1)) is None


def test_a_state_signed_with_another_key_is_rejected() -> None:
    cookie = sign_state("another-secret", _state())

    assert read_state(SECRET, cookie, now=NOW) is None


def test_a_changed_payload_is_rejected() -> None:
    cookie = sign_state(SECRET, _state(next="/a"))
    other = sign_state(SECRET, _state(next="/b"))
    payload, _ = cookie.split(".")
    _, other_signature = other.split(".")

    assert read_state(SECRET, f"{payload}.{other_signature}", now=NOW) is None


def test_a_changed_signature_is_rejected() -> None:
    payload, signature = sign_state(SECRET, _state()).split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]

    assert read_state(SECRET, f"{payload}.{flipped}", now=NOW) is None


@pytest.mark.parametrize(
    "garbage",
    ["", ".", "no-dot", "a.b", "a.b.c", "!!!.???", "e30.e30", "bm90LWpzb24.AAAA"],
)
def test_garbage_is_rejected(garbage: str) -> None:
    assert read_state(SECRET, garbage, now=NOW) is None


def test_a_missing_cookie_is_rejected() -> None:
    assert read_state(SECRET, None, now=NOW) is None


def test_a_correctly_signed_payload_with_wrong_fields_is_rejected() -> None:
    # Whatever signs the cookie, the reader checks the shape instead of trusting it.
    forged = _seal(SECRET, {"state": 1, "provider": "github"})

    assert read_state(SECRET, forged, now=NOW) is None


def test_the_state_in_a_cookie_is_checked_again_on_read() -> None:
    # `next` was already sanitised when the cookie was made; a reader re-checks it anyway.
    cookie = sign_state(SECRET, _state(next="https://evil.example"))

    state = read_state(SECRET, cookie, now=NOW)

    assert state is not None
    assert state.next == "/"


# --- matching the callback to the state ------------------------------------------------------


def test_state_matches_only_the_same_provider_and_value() -> None:
    state = _state()

    assert state_matches(state, "github", "state-value")
    assert not state_matches(state, "google", "state-value")
    assert not state_matches(state, "github", "other")
    assert not state_matches(state, "github", "")
    assert not state_matches(state, "github", None)


# --- the cookie's attributes -----------------------------------------------------------------


def _settings(base_url: str) -> Settings:
    return Settings(app_base_url=base_url, secret_key=SECRET, _env_file=None)


def test_the_cookie_is_http_only_lax_and_scoped_to_the_oauth_routes() -> None:
    response = Response()

    set_state_cookie(response, _settings("http://localhost:8000"), _state())

    header = response.headers["set-cookie"].lower()
    assert header.startswith(f"{STATE_COOKIE}=")
    for attribute in ("httponly", "samesite=lax", "path=/api/v1/auth/oauth", "max-age=600"):
        assert attribute in header
    assert "secure" not in header.split("; ")


def test_the_cookie_is_secure_behind_https() -> None:
    response = Response()

    set_state_cookie(response, _settings("https://spanlight.example"), _state())

    assert "secure" in response.headers["set-cookie"].lower().split("; ")


def test_clearing_expires_the_cookie_on_the_same_path() -> None:
    response = Response()

    clear_state_cookie(response, _settings("https://spanlight.example"))

    header = response.headers["set-cookie"].lower()
    assert "max-age=0" in header
    assert "path=/api/v1/auth/oauth" in header


# --- safe_next ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/", "/"),
        ("/dashboard", "/dashboard"),
        ("/settings/security", "/settings/security"),
        ("/traces?env=prod&x=1", "/traces?env=prod&x=1"),
        ("/page#section", "/page#section"),
        ("/a/b/c", "/a/b/c"),
        ("/trailing-backslash-is-just-a-char\\later", "/trailing-backslash-is-just-a-char\\later"),
        (None, "/"),
        ("", "/"),
        ("dashboard", "/"),
        ("?x=1", "/"),
        ("#x", "/"),
        (" /dashboard", "/"),
        ("//evil", "/"),
        ("//evil.example/path", "/"),
        ("///evil", "/"),
        ("/\\evil", "/"),
        ("/\\\\evil", "/"),
        ("\\\\evil", "/"),
        ("\\evil", "/"),
        ("https://evil.example", "/"),
        ("http://evil.example/", "/"),
        ("HTTPS://evil.example", "/"),
        ("javascript:alert(1)", "/"),
        ("data:text/html,<script>", "/"),
        ("mailto:a@b.c", "/"),
        ("/ok\r\nSet-Cookie: pwned=1", "/"),
        ("/ok\nrest", "/"),
        ("/ok\rrest", "/"),
        ("/\t/evil.example", "/"),
        ("/\x00evil", "/"),
        ("/\x7fevil", "/"),
        ("/" + "a" * 3000, "/"),
    ],
)
def test_safe_next(raw: str | None, expected: str) -> None:
    assert safe_next(raw) == expected
