"""Secret redaction (pure)."""

import json

import pytest

from app.core.redact import REDACTED, luhn_valid, redact_json, redact_text
from app.core.security import KEY_PREFIXES, generate_key

# Real credentials of every kind, made the way the app makes them.
CREDENTIALS = [generate_key(prefix).plaintext for prefix in KEY_PREFIXES]
PAT = "spl_pat_abcdefghijkl_abcdefghijklmnopqrstuvwxyz234567"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-AbCdEfGhIjKlMnOp_qrstu-vwxyz",
        "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
        "sk-abcdefghijklmnopqrstuvwx",
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
        "AKIAIOSFODNN7EXAMPLE",
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "spl_live_abcdefghijkl_abcdefghijklmnopqrstuvwxyz234567",
        PAT,
        "spl_gw_abcdefghijkl_abcdefghijklmnopqrstuvwxyz234567",
        "4111 1111 1111 1111",
        "5500-0000-0000-0004",
    ],
)
def test_secrets_are_redacted(secret: str) -> None:
    redacted = redact_text(f"prefix {secret} suffix")
    assert secret not in redacted
    assert redacted.startswith("prefix ") and redacted.endswith(" suffix")
    assert REDACTED in redacted


@pytest.mark.parametrize(
    "harmless",
    [
        "Order 1234567890123 shipped",  # 13 digits, fails Luhn
        "skip the task-based approach",
        "The bearer of bad news",
        "call 555-0100 tomorrow",
    ],
)
def test_ordinary_text_is_untouched(harmless: str) -> None:
    assert redact_text(harmless) == harmless


def test_luhn() -> None:
    assert luhn_valid("4242424242424242")
    assert not luhn_valid("4242424242424241")


def test_redact_json_walks_nested_values_and_keys() -> None:
    value = {
        "headers": {"authorization": "Bearer abcdefghijklmnop"},
        "items": [1, None, True, "sk-ant-abcdefghijklmnop"],
        "sk-abcdefghijklmnopqrstuvwx": "key used as a key",
    }
    assert redact_json(value) == {
        "headers": {"authorization": REDACTED},
        "items": [1, None, True, REDACTED],
        REDACTED: "key used as a key",
    }


@pytest.mark.parametrize("credential", CREDENTIALS, ids=KEY_PREFIXES)
def test_every_kind_of_spanlight_credential_is_redacted(credential: str) -> None:
    assert redact_text(f"token: {credential}.") == f"token: {REDACTED}."


def test_the_credential_kinds_are_the_ones_security_defines() -> None:
    # One list feeds the generator, the parser and this redaction: a new prefix is covered the
    # day it is added.
    assert KEY_PREFIXES == ("spl_live_", "spl_pat_", "spl_gw_")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Bare in prose.
        (f"use {PAT} to call the API", f"use {REDACTED} to call the API"),
        # In JSON text, where a regex anchored on `Bearer` would never look.
        (f'{{"token": "{PAT}", "ok": true}}', f'{{"token": "{REDACTED}", "ok": true}}'),
        # In a URL query string.
        (
            f"https://api.example.com/v1?token={PAT}&limit=5",
            f"https://api.example.com/v1?token={REDACTED}&limit=5",
        ),
        # An environment dump, one variable per line.
        (
            f"HOME=/root\nSPANLIGHT_TOKEN={PAT}\nPATH=/usr/bin",
            f"HOME=/root\nSPANLIGHT_TOKEN={REDACTED}\nPATH=/usr/bin",
        ),
        # Two in one string, and one next to an API key.
        (f"{PAT} {PAT}", f"{REDACTED} {REDACTED}"),
        (
            f"SPANLIGHT_KEY=spl_live_abcdefghijkl_abcdefghijklmnopqrstuvwxyz234567 T={PAT}",
            f"SPANLIGHT_KEY={REDACTED} T={REDACTED}",
        ),
    ],
)
def test_a_bare_token_is_redacted_wherever_it_appears(text: str, expected: str) -> None:
    assert redact_text(text) == expected


def test_a_token_in_json_values_and_keys_is_redacted() -> None:
    value = {"env": {"SPANLIGHT_TOKEN": PAT}, "args": [{"token": PAT}], PAT: "as a key"}

    assert PAT not in json.dumps(redact_json(value))
    assert redact_json(value) == {
        "env": {"SPANLIGHT_TOKEN": REDACTED},
        "args": [{"token": REDACTED}],
        REDACTED: "as a key",
    }


@pytest.mark.parametrize(
    "harmless",
    [
        "spl_pat_",
        "tokens start with spl_pat_ and keys with spl_live_",
        "spl_pat_abcdefghijkl",  # a prefix and an id, no secret
        "spl_pat_abc_def",
        "spl_pat_abcdefghijkl_abcdefghijklmnopqrstuvwxyz23456",  # secret one short
        "spl_pat_abcdefghijk_abcdefghijklmnopqrstuvwxyz234567",  # id one short
        "spl_pat_ABCDEFGHIJKL_ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",  # base32 here is lowercase
        "spl_pat_abcdefghijkl_abcdefghijklmnopqrstuvwxyz234561",  # 1 is not in the alphabet
        "spl_pat_abcdefghijkl-abcdefghijklmnopqrstuvwxyz234567",  # no underscore
        "spl_xyz_abcdefghijkl_abcdefghijklmnopqrstuvwxyz234567",  # not one of ours
        "spl_pat",
    ],
)
def test_text_that_only_resembles_a_credential_is_left_alone(harmless: str) -> None:
    assert redact_text(harmless) == harmless
