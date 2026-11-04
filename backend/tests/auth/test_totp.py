"""TOTP codes (RFC 6238) and recovery codes: the pure functions, with no database.

Time is always passed in, so every case here is deterministic.
"""

import hashlib
import re
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from app.auth.totp import (
    STEP_SECONDS,
    code_for_step,
    hash_recovery_code,
    is_recovery_code,
    is_totp_code,
    match_step,
    new_recovery_codes,
    new_secret,
    normalize_code,
    provisioning_url,
    step_at,
)

# The shared secret of RFC 6238 appendix B (ASCII "12345678901234567890") in base32.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
NOW = datetime(2026, 10, 8, 12, 0, 10, tzinfo=UTC)
RECOVERY_SHAPE = re.compile(r"^[a-z2-7]{5}-[a-z2-7]{5}$")


def at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


# --- codes -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("unix_time", "expected"),
    [
        # RFC 6238 appendix B, SHA-1, the last six of the published eight digits.
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
        (20000000000, "353130"),
    ],
)
def test_codes_match_the_rfc_6238_test_vectors(unix_time: int, expected: str) -> None:
    assert code_for_step(RFC_SECRET, step_at(at(unix_time))) == expected


def test_a_step_lasts_thirty_seconds() -> None:
    assert STEP_SECONDS == 30
    assert step_at(at(0)) == 0
    assert step_at(at(29)) == 0
    assert step_at(at(30)) == 1
    assert step_at(at(59)) == 1
    assert step_at(at(60)) == 2


def test_the_step_is_taken_in_utc_whatever_the_zone_of_the_datetime() -> None:
    plus_five = datetime(2026, 10, 8, 17, 0, 10, tzinfo=timezone(timedelta(hours=5)))
    assert step_at(plus_five) == step_at(NOW)


def test_a_new_secret_is_160_bits_of_base32_and_never_repeats() -> None:
    secrets_made = {new_secret() for _ in range(50)}

    assert len(secrets_made) == 50
    for secret in secrets_made:
        assert re.fullmatch(r"[A-Z2-7]{32}", secret)


def test_the_provisioning_url_names_the_issuer_and_labels_the_account_with_the_email() -> None:
    url = provisioning_url("JBSWY3DPEHPK3PXP", "ada@example.com")

    parts = urlsplit(url)
    assert parts.scheme == "otpauth"
    assert parts.netloc == "totp"
    assert unquote(parts.path) == "/Spanlight:ada@example.com"
    query = parse_qs(parts.query)
    assert query["secret"] == ["JBSWY3DPEHPK3PXP"]
    assert query["issuer"] == ["Spanlight"]
    # The defaults (SHA-1, 6 digits, 30 s) are left out, as authenticator apps expect.
    assert "algorithm" not in query
    assert "digits" not in query
    assert "period" not in query


# --- matching ----------------------------------------------------------------------------------


def test_the_current_code_matches_and_returns_its_step() -> None:
    step = step_at(NOW)
    code = code_for_step(RFC_SECRET, step)

    assert match_step(RFC_SECRET, code, NOW, None) == step


@pytest.mark.parametrize("offset", [-1, 1])
def test_one_step_either_side_is_accepted_for_clock_drift(offset: int) -> None:
    step = step_at(NOW) + offset
    code = code_for_step(RFC_SECRET, step)

    assert match_step(RFC_SECRET, code, NOW, None) == step


@pytest.mark.parametrize("offset", [-2, 2, -10, 10])
def test_two_or_more_steps_away_is_refused(offset: int) -> None:
    code = code_for_step(RFC_SECRET, step_at(NOW) + offset)

    assert match_step(RFC_SECRET, code, NOW, None) is None


def test_a_code_for_a_step_not_after_the_last_used_step_is_refused() -> None:
    step = step_at(NOW)
    code = code_for_step(RFC_SECRET, step)

    # The step itself was already used: this is the replay protection.
    assert match_step(RFC_SECRET, code, NOW, step) is None
    # An older step inside the window is refused as well.
    assert match_step(RFC_SECRET, code, NOW, step + 1) is None
    # One step before is the only thing that lets the same code through.
    assert match_step(RFC_SECRET, code, NOW, step - 1) == step


def test_with_a_used_current_step_the_next_steps_code_still_works() -> None:
    step = step_at(NOW)
    next_code = code_for_step(RFC_SECRET, step + 1)

    assert match_step(RFC_SECRET, next_code, NOW, step) == step + 1


def test_a_wrong_code_does_not_match() -> None:
    right = code_for_step(RFC_SECRET, step_at(NOW))
    wrong = f"{(int(right) + 1) % 1_000_000:06d}"
    # Make sure the wrong code is not accidentally the previous or next step's.
    neighbours = {code_for_step(RFC_SECRET, step_at(NOW) + n) for n in (-1, 0, 1)}
    assert wrong not in neighbours

    assert match_step(RFC_SECRET, wrong, NOW, None) is None


def test_a_code_for_another_secret_does_not_match() -> None:
    other = new_secret()
    code = code_for_step(other, step_at(NOW))

    assert match_step(RFC_SECRET, code, NOW, None) is None


@pytest.mark.parametrize(
    "junk", ["", "12345", "1234567", "abcdef", "12 3456x", "\u0660" * 6, "1e5555"]
)
def test_input_that_is_not_six_digits_never_matches(junk: str) -> None:
    assert match_step(RFC_SECRET, junk, NOW, None) is None


def test_spaces_and_hyphens_in_the_typed_code_are_ignored() -> None:
    step = step_at(NOW)
    code = code_for_step(RFC_SECRET, step)

    assert match_step(RFC_SECRET, f" {code[:3]} {code[3:]} ", NOW, None) == step
    assert match_step(RFC_SECRET, f"{code[:3]}-{code[3:]}", NOW, None) == step


# --- normalising and classifying what the user typed -------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123456", "123456"),
        (" 123 456 ", "123456"),
        ("123-456", "123456"),
        ("ABCDE-FGHIJ", "abcdefghij"),
        ("  abcde fghij\n", "abcdefghij"),
        ("", ""),
    ],
)
def test_normalize_code(raw: str, expected: str) -> None:
    assert normalize_code(raw) == expected


def test_a_totp_code_is_exactly_six_ascii_digits() -> None:
    assert is_totp_code("012345")
    assert not is_totp_code("12345")
    assert not is_totp_code("1234567")
    assert not is_totp_code("12345a")
    # str.isdigit() is true for these; they must not count as digits here.
    assert not is_totp_code("\u0661\u0662\u0663\u0664\u0665\u0666")  # Arabic-Indic digits
    assert not is_totp_code("\u00b2" * 6)  # superscript two


@pytest.mark.parametrize("code", ["abcdefghij", "a2b3c4d5e6", "abcdefghijklmnopqrstuvwxyz23"])
def test_a_recovery_code_is_ten_or_twenty_eight_characters_of_lowercase_base32(code: str) -> None:
    assert is_recovery_code(code)


@pytest.mark.parametrize("length", [9, 11, 27, 29])
def test_other_recovery_code_lengths_are_refused(length: int) -> None:
    assert not is_recovery_code("a" * length)


@pytest.mark.parametrize("character", ["0", "1", "8", "9"])
def test_a_current_recovery_code_rejects_characters_outside_the_alphabet(character: str) -> None:
    assert not is_recovery_code("abcdefghijklmnopqrstuvwxyz2" + character)


def test_a_recovery_code_requires_normalized_lowercase_base32() -> None:
    assert not is_recovery_code("abcdefghi1")  # 1 is not in the alphabet
    assert not is_recovery_code("abcdefghi0")
    assert not is_recovery_code("ABCDEFGHIJ")  # normalise first


def test_a_formatted_current_recovery_code_normalizes_to_twenty_eight_characters() -> None:
    normalized = normalize_code("  ABCDEFG-hijklmn-opqrstu-vwxyz23\t\n")

    assert normalized == "abcdefghijklmnopqrstuvwxyz23"
    assert len(normalized) == 28


# --- recovery codes ----------------------------------------------------------------------------


def test_ten_distinct_recovery_codes_in_display_form() -> None:
    codes = new_recovery_codes()

    assert len(codes) == 10
    assert len(set(codes)) == 10
    for code in codes:
        assert RECOVERY_SHAPE.fullmatch(code), code


def test_recovery_codes_are_not_repeated_between_calls() -> None:
    assert not set(new_recovery_codes()) & set(new_recovery_codes())


def test_a_recovery_code_hashes_to_sha256_of_its_ten_characters() -> None:
    assert hash_recovery_code("abcde-fghij") == hashlib.sha256(b"abcdefghij").digest()


@pytest.mark.parametrize("typed", ["abcde-fghij", "ABCDE-FGHIJ", " abcdefghij ", "abcde fghij"])
def test_the_hash_does_not_depend_on_how_the_code_was_typed(typed: str) -> None:
    assert hash_recovery_code(typed) == hash_recovery_code("abcde-fghij")


def test_different_codes_have_different_hashes() -> None:
    assert hash_recovery_code("abcde-fghij") != hash_recovery_code("abcde-fghik")
