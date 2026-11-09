"""Secret redaction (pure)."""

import json
import random
import re
import string
import sys
import time

import pytest

from app.core import redact
from app.core.redact import REDACTED, luhn_valid, redact_json, redact_text
from app.core.security import KEY_PREFIXES, generate_key
from app.ingest.schemas import MAX_BODY_BYTES, SpanIn
from app.ingest.trace_fields import trace_fields
from tests.core import test_redact_cards
from tests.core.test_redact_cards import skip_when_traced
from tests.helpers import new_trace_id, span

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


# Third-party credentials, assembled at runtime so the file itself holds no token-shaped literal.
_ALNUM = "A1b2C3d4E5f6G7h8I9j0"
NEW_SECRETS = {
    "stripe secret": "sk_" + "live_" + _ALNUM + "KlMn",
    "stripe restricted": "rk_" + "live_" + _ALNUM + "KlMn",
    **{f"github {kind}": f"gh{kind}_" + (_ALNUM * 2)[:36] for kind in "pousr"},
    "github fine-grained": "github_" + "pat_" + "11ABCDEFG0" + _ALNUM + "_" + _ALNUM * 3,
    "google": "AI" + "za" + (_ALNUM * 2)[:35],
    **{f"slack {kind}": f"xox{kind}-" + "123456789012-1234567890123-" + _ALNUM for kind in "bpars"},
    "groq": "gsk_" + _ALNUM * 2 + "AbCdEfGhIjKl",
    "jwt": "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxIn0" + "." + "c2lnbmF0dXJlLXNpZw",
    "pem rsa": "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1t\n"
    "Pf9Cnzj4p4WGeKLs1Pt8Qu+CqvjWYzvQwECAwEAAQ==\n-----END RSA PRIVATE KEY-----",
    "pem pkcs8": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n"
    "-----END PRIVATE KEY-----",
    "pem openssh": "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n"
    "-----END OPENSSH PRIVATE KEY-----",
    "pem legacy encrypted": "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\n"
    "DEK-Info: AES-128-CBC,0123456789ABCDEF\n\nMIIBOgIBAAJBAKj34Gkx\n"
    "-----END RSA PRIVATE KEY-----",
    "x-api-key header": "x-api-key: " + _ALNUM,
    "x-api-key header, any case": "X-API-Key: " + _ALNUM,
    "x-api-key assignment": "X-Api-Key=" + _ALNUM,
}

NEW_NEAR_MISSES = [
    "sk_" + "live_" + "short",  # too short
    "sk_" + "test_" + _ALNUM,  # a test key, not a live one
    "rk_" + "live_",
    "ghp_" + _ALNUM[:10],  # too short
    "ghx_" + (_ALNUM * 2)[:36],  # not a GitHub token kind
    "github_" + "pat_" + "short",
    "AI" + "za" + (_ALNUM * 2)[:34],  # one short
    "AI" + "za" + "Sy-with spaces in it",
    "xoxb-short",
    "xoxz-" + "123456789012-1234567890123-" + _ALNUM,  # not a Slack token kind
    "gsk_" + "short",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0",  # no signature
    "eyJhbGciOiJIUzI1NiJ9.notapayload.sig",
    "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOC\n-----END PUBLIC KEY-----",
    "-----BEGIN CERTIFICATE-----\nMIIDdzCCAl+gAwIBAgIEAgAAuTANBgkq\n-----END CERTIFICATE-----",
    "x-api-key: short",  # too short
    "x-api-keys are rotated every month",
    # Glued to a letter or digit on the left: part of base64 or a longer identifier.
    "Q" + "AI" + "za" + (_ALNUM * 2)[:35],
    "7ghp_" + (_ALNUM * 2)[:36],
    "xgithub_" + "pat_" + "11ABCDEFG0" + _ALNUM,
    "Ask_" + "live_" + _ALNUM + "KlMn",
    "9rk_" + "live_" + _ALNUM + "KlMn",
]


@pytest.mark.parametrize("secret", NEW_SECRETS.values(), ids=NEW_SECRETS.keys())
def test_third_party_secrets_are_redacted(secret: str) -> None:
    assert redact_text(f"prefix {secret} suffix") == f"prefix {REDACTED} suffix"


def test_an_x_api_key_value_in_json_text_is_redacted() -> None:
    text = '{"x-api-key": "' + _ALNUM + '", "accept": "json"}'

    assert redact_text(text) == '{"' + REDACTED + '", "accept": "json"}'


@pytest.mark.parametrize("harmless", NEW_NEAR_MISSES)
def test_near_misses_of_third_party_secrets_are_left_alone(harmless: str) -> None:
    assert redact_text(harmless) == harmless


def _ungated(value: str, *, contacts: bool = False) -> str:
    """The reference: every pattern run over every string, with no gate in front of any."""
    for pattern, _gates in redact._SECRET_PATTERNS:
        value = pattern.sub(REDACTED, value)
    if contacts:
        value = redact._PHONE.sub(redact._redact_phone, redact._EMAIL.sub(REDACTED, value))
    return redact._DIGIT_RUN.sub(redact._redact_cards, value)


def _fixture_strings() -> list[str]:
    """Every string parametrized into this module's and the card module's tests."""
    found: list[str] = []
    for module in (sys.modules[__name__], test_redact_cards):
        for test in vars(module).values():
            for mark in getattr(test, "pytestmark", []):
                if mark.name != "parametrize":
                    continue
                for case in mark.args[1]:
                    found.extend(c for c in (case if isinstance(case, tuple) else (case,)))
    return [value for value in found if isinstance(value, str)]


def _generated_corpus() -> list[str]:
    """Random text with and without each gate, its secret, a near miss and case variants."""
    rng = random.Random(20261101)  # noqa: S311
    gates = sorted(
        {gate for _pattern, pattern_gates in redact._SECRET_PATTERNS for gate in pattern_gates}
    )
    prefixes = [
        *KEY_PREFIXES,
        *("sk-ant-", "sk-", "sk_" + "live_", "rk_" + "live_", "github_" + "pat_", "AIza", "gsk_"),
        *(f"gh{kind}_" for kind in "pousr"),
        *(f"xox{kind}-" for kind in "bpars"),
        "eyJ",
        "eyJa.eyJb.",
        "-----BEGIN EC PRIVATE KEY-----\n",
        "x-api-key: ",
    ]
    pieces = [
        *gates,
        *(gate.upper() for gate in gates),
        *prefixes,
        *NEW_SECRETS.values(),
        *NEW_NEAR_MISSES,
        *CREDENTIALS,
        "Bearer abcdefghijklmnop",
        "BEARER\tabcdefghijklmnop",
        "aws_\u017fecret_access_key = " + "a" * 40,  # long s matches `s` case-insensitively
        "\u212a",  # the Kelvin sign matches `k` case-insensitively
        "AKIAIOSFODNN7EXAMPLE",
        "jane@example.com",
        "+44 20 7946 0958",
        "4242 4242 4242 4242",
        "-----END RSA PRIVATE KEY-----",
        "[REDACTED]",
    ]
    filler = string.ascii_letters + string.digits + string.punctuation + " \n\t\u00e9\u0131\u017f"
    corpus = []
    for _ in range(3000):
        parts = []
        for _ in range(rng.randint(1, 8)):
            if rng.random() < 0.5:
                parts.append(rng.choice(pieces))
            else:
                parts.append("".join(rng.choices(filler, k=rng.randint(0, 40))))
            if rng.random() < 0.3:  # glue a random tail on, to make longer or broken variants
                parts.append("".join(rng.choices(_ALNUM + "-_.", k=rng.randint(0, 60))))
        corpus.append(rng.choice(["", " ", "\n"]).join(parts))
    return corpus


def test_gated_redaction_matches_ungated_output() -> None:
    strings = _fixture_strings() + _generated_corpus()
    assert len(strings) > 3000

    for text in strings:
        for contacts in (False, True):
            assert redact_text(text, contacts=contacts) == _ungated(text, contacts=contacts), text


def test_case_insensitive_gates_are_found_in_the_lowercased_text() -> None:
    # A case-insensitive pattern is gated on `value.lower()`. That is sound only when every
    # character the pattern matches for a gate character lowercases to it, which rules out gate
    # letters with extra Unicode equivalents (`s` and the long s, `k` and the Kelvin sign, `i`
    # and the dotless i).
    every_character = "".join(map(chr, range(0xD800))) + "".join(map(chr, range(0xE000, 0x110000)))
    for pattern, gates in redact._SECRET_PATTERNS:
        if not pattern.flags & re.IGNORECASE:
            continue
        for character in set("".join(gates)):
            assert character == character.lower(), pattern.pattern
            for match in re.finditer(re.escape(character), every_character, re.IGNORECASE):
                assert match.group().lower() == character, (pattern.pattern, match.group())


def test_no_secret_pattern_matches_fewer_characters_than_the_short_string_skip() -> None:
    # `redact_text` skips the secret patterns for a string shorter than `_SHORTEST_SECRET`.
    for pattern, _gates in redact._SECRET_PATTERNS:
        shortest = re._parser.parse(pattern.pattern, pattern.flags).getwidth()[0]  # type: ignore[attr-defined]
        assert shortest >= redact._SHORTEST_SECRET, pattern.pattern


@skip_when_traced
@pytest.mark.parametrize("unit", ["eyJ", "eyJ-", "eyJa.eyJ"])
def test_a_body_of_repeated_jwt_starts_stays_cheap(unit: str) -> None:
    # Every `eyJ` used to be a start that scanned to the end of the run and back.
    text = unit * (MAX_BODY_BYTES // len(unit))

    started = time.process_time()
    redact_text(text)

    assert time.process_time() - started < 5.0


@skip_when_traced
def test_a_body_of_unterminated_private_key_headers_stays_cheap() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIB " * (MAX_BODY_BYTES // 37)

    started = time.process_time()
    assert redact_text(text) == text

    assert time.process_time() - started < 5.0


def test_a_credential_named_by_its_json_key_is_redacted() -> None:
    """A header recorded as a JSON pair (OTel `http.request.header.*` attributes, SDK
    `{"headers": {...}}` metadata) splits the label from the value, so the label-and-value text
    patterns never saw it: the key names the credential, and its value is replaced."""
    secret = "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"
    body = {
        "x-api-key": "abcdefgh12345678zz",
        "aws_secret_access_key": secret,
        "headers": {"Authorization": "Basic dXNlcjpwYXNzd29yZA=="},
        "http.request.header.x-api-key": ["abcdefgh12345678zz"],
        "authorization_mode": "basic",
    }
    assert redact_json(body) == {
        "x-api-key": REDACTED,
        "aws_secret_access_key": REDACTED,
        "headers": {"Authorization": REDACTED},
        "http.request.header.x-api-key": [REDACTED],
        "authorization_mode": "basic",
    }
    assert "dXNl" not in redact_text('{"Authorization": "Basic dXNlcjpwYXNzd29yZA=="}')
    assert "dTpw" not in redact_text("Authorization: Basic dTpw")  # `u:p`
    assert redact_text("a basic introduction") == "a basic introduction"
    # The whole value, whatever its shape.
    assert redact_json(
        {"authorization": {"value": "dXNlcjpwYXNzd29yZA=="}, "x-api-key": [12345678, ["k3y"]]}
    ) == {"authorization": {"value": REDACTED}, "x-api-key": [REDACTED, [REDACTED]]}
    # Trace metadata is a flat string map redacted value by value.
    metadata = {"authorization": "Basic dXNlcjpwYXNzd29yZA==", "x-api-key": "opaque", "plan": "pro"}
    fields = trace_fields(
        SpanIn.model_validate(span(trace_id=new_trace_id(), trace={"metadata": metadata}))
    )
    assert fields.metadata == {"authorization": REDACTED, "x-api-key": REDACTED, "plan": "pro"}
