"""Application-level encryption: AES-256-GCM sealing under a keyring from CREDENTIALS_KEYS.

Everything here uses the real `cryptography` primitives and the real `Settings` class. Settings are
built with `_env_file=None` so a developer's local `.env` cannot change the outcome.
"""

import base64
import secrets

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.core.crypto import (
    CryptoNotConfigured,
    DecryptionFailed,
    Sealed,
    UnknownKeyId,
    decrypt,
    encrypt,
    parse_keyring,
)

NONCE_SIZE = 12
TAG_SIZE = 16


def b64_key(raw: bytes | None = None) -> str:
    """A standard-alphabet, padded base64 key, the shape `openssl rand -base64 32` prints."""
    return base64.b64encode(raw if raw is not None else secrets.token_bytes(32)).decode()


def settings_for(*entries: str) -> Settings:
    return Settings(_env_file=None, credentials_keys=",".join(entries))


def make_settings(**ids_to_keys: str) -> Settings:
    return settings_for(*(f"{key_id}:{key}" for key_id, key in ids_to_keys.items()))


def unconfigured() -> Settings:
    # Explicit None, so a CREDENTIALS_KEYS exported in the developer's shell cannot leak in.
    return Settings(_env_file=None, credentials_keys=None)


# --- sealing and opening ----------------------------------------------------------------------


def test_round_trip() -> None:
    settings = make_settings(v1=b64_key())
    sealed = encrypt(b"JBSWY3DPEHPK3PXP", settings=settings)
    assert decrypt(sealed, settings=settings) == b"JBSWY3DPEHPK3PXP"


def test_round_trip_of_an_empty_plaintext() -> None:
    settings = make_settings(v1=b64_key())
    assert decrypt(encrypt(b"", settings=settings), settings=settings) == b""


def test_sealed_layout_is_nonce_then_ciphertext_and_tag() -> None:
    settings = make_settings(v1=b64_key())
    plaintext = b"x" * 40
    sealed = encrypt(plaintext, settings=settings)
    assert isinstance(sealed, Sealed)
    assert sealed.key_id == "v1"
    assert len(sealed.ciphertext) == NONCE_SIZE + len(plaintext) + TAG_SIZE
    assert plaintext not in sealed.ciphertext


def test_the_same_plaintext_seals_differently_each_time() -> None:
    settings = make_settings(v1=b64_key())
    first = encrypt(b"same", settings=settings)
    second = encrypt(b"same", settings=settings)
    assert first.ciphertext != second.ciphertext
    assert first.ciphertext[:NONCE_SIZE] != second.ciphertext[:NONCE_SIZE]


def test_encrypt_uses_the_first_key() -> None:
    settings = make_settings(v2=b64_key(), v1=b64_key())
    assert encrypt(b"x", settings=settings).key_id == "v2"


def test_a_value_sealed_under_v1_opens_after_v2_becomes_active() -> None:
    v1, v2 = b64_key(), b64_key()
    sealed = encrypt(b"old secret", settings=make_settings(v1=v1))

    rotated = make_settings(v2=v2, v1=v1)
    assert decrypt(sealed, settings=rotated) == b"old secret"
    # New writes use the new key, and both generations coexist.
    assert encrypt(b"new secret", settings=rotated).key_id == "v2"


# --- tampering and wrong keys -----------------------------------------------------------------


@pytest.mark.parametrize("position", [0, NONCE_SIZE - 1, NONCE_SIZE, -TAG_SIZE, -1])
def test_flipping_one_ciphertext_byte_fails_decryption(position: int) -> None:
    settings = make_settings(v1=b64_key())
    sealed = encrypt(b"a secret long enough to span bytes", settings=settings)
    tampered = bytearray(sealed.ciphertext)
    tampered[position] ^= 0x01

    with pytest.raises(DecryptionFailed):
        decrypt(Sealed(bytes(tampered), sealed.key_id), settings=settings)


@pytest.mark.parametrize("length", [0, 1, NONCE_SIZE - 1, NONCE_SIZE, NONCE_SIZE + TAG_SIZE - 1])
def test_a_ciphertext_too_short_to_hold_a_nonce_and_tag_fails_decryption(length: int) -> None:
    settings = make_settings(v1=b64_key())
    with pytest.raises(DecryptionFailed):
        decrypt(Sealed(b"\x00" * length, "v1"), settings=settings)


def test_a_ciphertext_presented_under_another_key_id_fails_decryption() -> None:
    settings = make_settings(v1=b64_key(), v2=b64_key())
    sealed = encrypt(b"secret", settings=settings)
    assert sealed.key_id == "v1"

    with pytest.raises(DecryptionFailed):
        decrypt(Sealed(sealed.ciphertext, "v2"), settings=settings)


def test_the_key_id_is_authenticated_even_when_two_ids_share_key_material() -> None:
    # Same key under two ids, so a wrong key cannot explain the failure: only the associated
    # data differs. This is what stops a row being re-labelled with another key id.
    shared = b64_key()
    settings = make_settings(v1=shared, v2=shared)
    sealed = encrypt(b"secret", settings=settings)

    with pytest.raises(DecryptionFailed):
        decrypt(Sealed(sealed.ciphertext, "v2"), settings=settings)
    assert decrypt(sealed, settings=settings) == b"secret"


def test_the_wrong_key_under_the_same_id_fails_decryption() -> None:
    sealed = encrypt(b"secret", settings=make_settings(v1=b64_key()))
    with pytest.raises(DecryptionFailed):
        decrypt(sealed, settings=make_settings(v1=b64_key()))


def test_a_key_id_that_is_not_configured_is_reported_as_unknown() -> None:
    sealed = encrypt(b"secret", settings=make_settings(v1=b64_key()))
    with pytest.raises(UnknownKeyId, match="v1"):
        decrypt(sealed, settings=make_settings(v2=b64_key()))


def test_failure_messages_never_carry_key_material_or_plaintext() -> None:
    key = b64_key()
    settings = make_settings(v1=key)
    plaintext = b"super-secret-totp-seed"
    sealed = encrypt(plaintext, settings=settings)
    tampered = Sealed(sealed.ciphertext[:-1] + bytes([sealed.ciphertext[-1] ^ 0x01]), sealed.key_id)

    with pytest.raises(DecryptionFailed) as failure:
        decrypt(tampered, settings=settings)
    with pytest.raises(UnknownKeyId) as unknown:
        decrypt(Sealed(sealed.ciphertext, "v9"), settings=settings)

    for error in (failure.value, unknown.value):
        assert key not in str(error)
        assert plaintext.decode() not in str(error)


# --- not configured ---------------------------------------------------------------------------


def test_an_unset_setting_means_crypto_is_not_configured() -> None:
    settings = unconfigured()
    assert settings.credentials_keys is None
    assert settings.is_crypto_configured is False

    with pytest.raises(CryptoNotConfigured, match="CREDENTIALS_KEYS"):
        encrypt(b"x", settings=settings)
    with pytest.raises(CryptoNotConfigured, match="CREDENTIALS_KEYS"):
        decrypt(Sealed(b"\x00" * 40, "v1"), settings=settings)


def test_a_configured_setting_reports_crypto_configured() -> None:
    assert make_settings(v1=b64_key()).is_crypto_configured is True


def test_a_blank_value_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None, credentials_keys="").credentials_keys is None

    monkeypatch.setenv("CREDENTIALS_KEYS", "")
    from_env = Settings(_env_file=None)
    assert from_env.credentials_keys is None
    assert from_env.is_crypto_configured is False


def test_the_setting_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    key = b64_key()
    monkeypatch.setenv("CREDENTIALS_KEYS", f"v1:{key}")
    settings = Settings(_env_file=None)
    assert decrypt(encrypt(b"x", settings=settings), settings=settings) == b"x"


# --- parsing the CREDENTIALS_KEYS format ------------------------------------------------------


def test_whitespace_around_entries_is_trimmed_and_order_is_kept() -> None:
    first, second = secrets.token_bytes(32), secrets.token_bytes(32)
    # `openssl rand -base64 32` ends its output with a newline, and people paste it as is.
    raw = f"  v2:{b64_key(first)}\n , v1:{b64_key(second)}\n"
    keyring = parse_keyring(raw)

    assert keyring.active_id == "v2"
    assert list(keyring.keys) == ["v2", "v1"]
    assert keyring.keys["v2"] == first
    assert keyring.keys["v1"] == second


def test_the_keyring_does_not_print_its_keys() -> None:
    raw = secrets.token_bytes(32)
    keyring = parse_keyring(f"v1:{b64_key(raw)}")
    assert b64_key(raw) not in repr(keyring)
    assert str(raw) not in repr(keyring)


@pytest.mark.parametrize("key_id", ["v1", "2026-10", "prod_key.a", "A", "k" * 64])
def test_key_ids_of_the_allowed_shape_are_accepted(key_id: str) -> None:
    assert parse_keyring(f"{key_id}:{b64_key()}").active_id == key_id


def _malformed_keyrings() -> dict[str, str]:
    good = b64_key()
    url_safe = base64.urlsafe_b64encode(b"\xfb" * 32).decode()  # contains "-" and "_"
    return {
        "bad base64 characters": f"v1:{good[:-4]}!!!!",
        "a 16 byte key": f"v1:{b64_key(secrets.token_bytes(16))}",
        "a 24 byte key": f"v1:{b64_key(secrets.token_bytes(24))}",
        "a 33 byte key": f"v1:{b64_key(secrets.token_bytes(33))}",
        "an empty key": "v1:",
        "missing padding": f"v1:{good.rstrip('=')}",
        "the url-safe alphabet": f"v1:{url_safe}",
        "embedded whitespace": f"v1:{good[:10]} {good[10:]}",
        "a duplicate key id": f"v1:{good},v1:{b64_key()}",
        "a missing colon": good,
        "an empty key id": f":{good}",
        "a key id with a space": f"my key:{good}",
        "a key id with a slash": f"a/b:{good}",
        "a key id with non-ascii letters": f"cl\u00e9:{good}",
        "a key id of 65 characters": f"{'k' * 65}:{good}",
        "a trailing comma": f"v1:{good},",
        "a leading comma": f",v1:{good}",
        "two commas in a row": f"v1:{good},,v2:{b64_key()}",
        "only whitespace": "   ",
        "a second entry that is bad": f"v1:{good},v2:{b64_key(secrets.token_bytes(16))}",
    }


# Parametrized with the label as the test id, never the value, so key material stays out of ids.
MALFORMED = _malformed_keyrings()


@pytest.mark.parametrize("raw", MALFORMED.values(), ids=list(MALFORMED))
def test_a_malformed_keyring_fails_settings_validation(raw: str) -> None:
    with pytest.raises(ValidationError, match="CREDENTIALS_KEYS"):
        Settings(_env_file=None, credentials_keys=raw)


def assert_no_fragment_of(secret: str, text: str, size: int = 8) -> None:
    """Fail if `text` holds any run of `size` characters of `secret`.

    pydantic shortens long inputs to their first and last characters when it prints an error, so
    checking for the whole secret would pass while most of it was still on screen.
    """
    for start in range(len(secret) - size + 1):
        assert secret[start : start + size] not in text


@pytest.mark.parametrize("raw", MALFORMED.values(), ids=list(MALFORMED))
def test_a_validation_error_never_echoes_the_secret_value(raw: str) -> None:
    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=None, credentials_keys=raw)

    # Every way a ValidationError can be rendered: logs use str, tools use errors() and json().
    rendered = " ".join(
        [
            str(failure.value),
            repr(failure.value),
            repr(failure.value.errors()),
            failure.value.json(),
        ]
    )
    for entry in raw.split(","):
        assert_no_fragment_of(entry.strip().partition(":")[2], rendered)
    if len(raw.strip()) >= 8:  # shorter than that is no key, and "   " is just indentation
        assert raw not in rendered


def test_an_unrelated_settings_error_does_not_echo_a_valid_keyring() -> None:
    # A model-level error prints a shortened copy of every setting that was passed in.
    key = b64_key()
    with pytest.raises(ValidationError, match="SECRET_KEY must be set") as failure:
        Settings(
            _env_file=None,
            app_base_url="https://spanlight.example.com",
            credentials_keys=f"v1:{key}",
        )
    assert_no_fragment_of(key, str(failure.value) + repr(failure.value))


def test_a_valid_keyring_is_not_shown_in_the_settings_repr() -> None:
    key = b64_key()
    settings = make_settings(v1=key)
    assert key not in repr(settings)
    assert key not in str(settings)
    assert key not in repr(settings.model_dump())


def test_parse_keyring_raises_a_value_error_naming_the_problem_without_the_value() -> None:
    key = b64_key(secrets.token_bytes(16))
    with pytest.raises(ValueError, match="32 bytes") as failure:
        parse_keyring(f"v1:{key}")
    assert key not in str(failure.value)
