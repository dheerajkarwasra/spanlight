"""Time-based one-time passwords (RFC 6238) and recovery codes, as pure functions.

No database, no settings and no clock of its own: time is always passed in, so everything here is
deterministic under test. The rules around them (who may enable, when a code is spent) are in
`app.auth.totp_service`.

The parameters are the ones every authenticator app assumes: HMAC-SHA1, six digits, 30-second
steps. A code is accepted for the current step and one step either side, to forgive a phone clock
that is a few seconds off, and only if its step is later than the last one used (see
`match_step`).
"""

import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime

import pyotp

ISSUER = "Spanlight"
STEP_SECONDS = 30
CODE_DIGITS = 6
# Steps accepted on each side of the current one.
WINDOW_STEPS = 1

SECRET_BYTES = 20  # 160 bits, the size RFC 4226 recommends; base32 of it is 32 characters
RECOVERY_CODE_COUNT = 10
LEGACY_RECOVERY_CODE_LENGTH = 10
CURRENT_RECOVERY_CODE_LENGTH = 28
RECOVERY_CODE_LENGTH = LEGACY_RECOVERY_CODE_LENGTH
# Lowercase base32 without padding: easy to read out and type, nothing that looks like another.
_RECOVERY_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_RECOVERY_GROUP = RECOVERY_CODE_LENGTH // 2

_TOTP_CODE = re.compile(r"[0-9]{6}")
_RECOVERY_CODE = re.compile(r"[a-z2-7]{10}|[a-z2-7]{28}")
_IGNORED_IN_INPUT = str.maketrans("", "", " -\t\r\n")


def new_secret() -> str:
    """A fresh random seed in the base32 form authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode()


def provisioning_url(secret: str, email: str) -> str:
    """The `otpauth://` URL for the QR code: issuer Spanlight, labelled with the account email."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def step_at(now: datetime) -> int:
    """The 30-second step a moment falls in (Unix time divided by 30). `now` must be aware."""
    return int(now.timestamp()) // STEP_SECONDS


def code_for_step(secret: str, step: int) -> str:
    """The six-digit code for one step."""
    return pyotp.TOTP(secret).generate_otp(step)


def match_step(secret: str, code: str, now: datetime, last_step: int | None) -> int | None:
    """The step `code` is valid for, or None.

    A code is valid if it is the code of the current step or one either side, and that step is
    later than `last_step`, the step of the last code accepted. That second rule is what stops a
    code from working twice: once step N is used, N and everything before it are dead, while the
    codes of steps still ahead of N keep working. The result is what the caller stores as the new
    `last_step`.

    All three candidates are always computed and compared, so the time taken does not say which
    one matched or whether any did.
    """
    normalized = normalize_code(code)
    if not is_totp_code(normalized):
        return None
    current = step_at(now)
    matched: list[int] = []
    for step in range(current - WINDOW_STEPS, current + WINDOW_STEPS + 1):
        if step < 0:
            continue
        if hmac.compare_digest(code_for_step(secret, step).encode(), normalized.encode()):
            matched.append(step)
    usable = [step for step in matched if last_step is None or step > last_step]
    return max(usable) if usable else None


def normalize_code(raw: str) -> str:
    """What was typed, minus spaces and hyphens, in lowercase: `ABCDE-fghij` is `abcdefghij`."""
    return raw.strip().lower().translate(_IGNORED_IN_INPUT)


def is_totp_code(normalized: str) -> bool:
    """Exactly six ASCII digits (not the other scripts' digits that `str.isdigit` accepts)."""
    return _TOTP_CODE.fullmatch(normalized) is not None


def is_recovery_code(normalized: str) -> bool:
    return _RECOVERY_CODE.fullmatch(normalized) is not None


def new_recovery_codes() -> list[str]:
    """Ten distinct random codes in the form people are shown, `abcde-fghij`."""
    codes: list[str] = []
    while len(codes) < RECOVERY_CODE_COUNT:
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))
        code = f"{raw[:_RECOVERY_GROUP]}-{raw[_RECOVERY_GROUP:]}"
        if code not in codes:
            codes.append(code)
    return codes


def hash_recovery_code(code: str) -> bytes:
    """SHA-256 of the normalized code, for both the 10- and 28-character formats.

    Lookup is per user, by user_id and code_hash; it does not rule out a salt. Current codes
    have 140 random bits, making offline search infeasible, so a slow or salted hash adds
    verification cost without meaningful protection. Legacy codes have only 50 random bits;
    their weakness is retired by replacement, not rehashing, since the plaintext is not stored.
    """
    return hashlib.sha256(normalize_code(code).encode()).digest()
