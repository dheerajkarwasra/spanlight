"""Credential primitives: password hashing, opaque tokens, CSRF tokens, prefixed keys."""

import asyncio
import hashlib
import hmac
import re
import secrets
import weakref
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from starlette.responses import Response

from app.core.thread_ownership import owned_thread

MIN_PASSWORD_LENGTH = 10

_password_hasher = PasswordHasher()  # argon2id with library-recommended parameters

# A real argon2 hash used to equalize timing when the email does not exist.
_DUMMY_PASSWORD_HASH = _password_hasher.hash("not-a-real-password-just-for-timing")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Return whether `password` matches.

    Pass `None` for an unknown account: a dummy hash is still verified so the
    response time does not reveal whether the email exists.
    """
    if password_hash is None:
        _verify_quietly(_DUMMY_PASSWORD_HASH, password)
        return False
    return _verify_quietly(password_hash, password)


# argon2id needs 64 MiB per hash. Hashing runs on a pool of its own, not the event loop's default
# executor (which also resolves hostnames for outgoing requests), and at most `_HASH_CONCURRENCY`
# hashes run at once however many requests ask: the rest wait as cheap coroutines, so a burst of
# sign-ins bounds memory at about 128 MiB instead of one hash per thread.
_HASH_CONCURRENCY = 2
_hash_executor = ThreadPoolExecutor(max_workers=_HASH_CONCURRENCY, thread_name_prefix="argon2")
# One semaphore per event loop: a semaphore must not be shared between loops (tests create many).
_hash_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


async def _run_hashing[T](function: Callable[..., T], *args: Any) -> T:
    loop = asyncio.get_running_loop()
    slots = _hash_slots.get(loop)
    if slots is None:
        slots = _hash_slots[loop] = asyncio.Semaphore(_HASH_CONCURRENCY)
    async with slots:
        return await owned_thread(loop.run_in_executor(_hash_executor, function, *args))


async def hash_password_async(password: str) -> str:
    """`hash_password` off the event loop, at most two at a time.

    argon2id is deliberately slow (tens of milliseconds and 64 MiB of memory), so running it
    inside an `async def` handler would stall every other request on the event loop. The hasher
    is thread-safe.
    """
    return await _run_hashing(hash_password, password)


async def verify_password_async(password_hash: str | None, password: str) -> bool:
    """`verify_password` off the event loop (same pool and bound), including the dummy
    verification for `None`."""
    return await _run_hashing(verify_password, password_hash, password)


def _verify_quietly(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# Stored for users who must never log in with a password (the demo user).
UNUSABLE_PASSWORD_HASH = "!unusable"  # noqa: S105 - not a secret; never a valid hash


def new_token() -> str:
    """32 random bytes, URL-safe. Used for session and invite tokens."""
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> bytes:
    """Tokens are stored only as their SHA-256 digest."""
    return hashlib.sha256(token.encode()).digest()


def new_csrf_token(secret_key: str, session_token: str) -> str:
    """A signed double-submit token bound to the session it was issued for."""
    nonce = secrets.token_urlsafe(18)
    return f"{nonce}.{_csrf_signature(secret_key, session_token, nonce)}"


def verify_csrf_token(secret_key: str, session_token: str, csrf_token: str) -> bool:
    nonce, separator, signature = csrf_token.partition(".")
    if not separator or not nonce or not signature:
        return False
    expected = _csrf_signature(secret_key, session_token, nonce)
    return hmac.compare_digest(expected, signature)


def _csrf_signature(secret_key: str, session_token: str, nonce: str) -> str:
    message = f"{token_digest(session_token).hex()}.{nonce}".encode()
    return hmac.new(secret_key.encode(), message, hashlib.sha256).hexdigest()


def mark_uncacheable(response: Response) -> None:
    """Keep a response that shows a secret once out of every cache (browser, proxy, history)."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def is_bearer(authorization: str | None) -> bool:
    """Whether an `Authorization` header value uses the Bearer scheme.

    Scheme names are case-insensitive (RFC 9110). Any other scheme, such as Basic or Negotiate,
    is none of this API's business: browsers attach those by themselves behind an authenticating
    proxy, so they must not switch off the cookie and Origin handling a Bearer request skips.
    The middleware and the dependencies both ask this one function, so they cannot disagree.
    """
    if authorization is None:
        return False
    scheme, _, _ = authorization.strip().partition(" ")
    return scheme.lower() == "bearer"


# Long-lived credentials look like `<prefix><12 char id>_<32 char secret>` in lowercase base32:
# `spl_live_…` for a project API key, `spl_pat_…` for a personal access token. The first part is
# stored and used to find the row, so it is safe to show; only the SHA-256 of the secret is kept.
# One implementation serves every kind (the LLM gateway adds `spl_gw_`), so the format, the
# lookup and the constant-time comparison cannot drift apart.
API_KEY_PREFIX = "spl_live_"
PAT_PREFIX = "spl_pat_"
GATEWAY_KEY_PREFIX = "spl_gw_"  # the key an application sends to the LLM gateway
# Every kind of credential. Anything that has to recognise a credential in free text, such as
# payload redaction, reads this tuple, so a new kind is covered the day it is added here.
KEY_PREFIXES = (API_KEY_PREFIX, PAT_PREFIX, GATEWAY_KEY_PREFIX)
_BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_KEY_ID_LENGTH = 12
_KEY_SECRET_LENGTH = 32

# A whole credential of any kind inside other text: `<prefix><id>_<secret>`, built from the same
# prefixes, alphabet and lengths that `generate_key` and `parse_key` use. Deliberately without
# word boundaries: a credential glued to other characters (`KEY=spl_pat_…`, `"spl_pat_…"`) is
# still a credential, and over-redacting is the safe side. Text that only starts like one
# (`spl_pat_` alone, or the wrong lengths) does not match.
ANY_KEY_PATTERN = re.compile(
    "(?:"
    + "|".join(re.escape(prefix) for prefix in KEY_PREFIXES)
    + f")[{_BASE32_ALPHABET}]{{{_KEY_ID_LENGTH}}}_[{_BASE32_ALPHABET}]{{{_KEY_SECRET_LENGTH}}}"
)


@dataclass(frozen=True)
class GeneratedKey:
    prefix: str  # `<kind prefix><id>`: stored, displayed and used for lookup
    secret_hash: bytes
    plaintext: str  # the full credential, shown to the user exactly once


@dataclass(frozen=True)
class ParsedKey:
    prefix: str
    secret: str


def _random_base32(length: int) -> str:
    return "".join(secrets.choice(_BASE32_ALPHABET) for _ in range(length))


def generate_key(prefix: str) -> GeneratedKey:
    """A new credential of the kind named by `prefix` (for example `PAT_PREFIX`)."""
    stored_prefix = prefix + _random_base32(_KEY_ID_LENGTH)
    secret = _random_base32(_KEY_SECRET_LENGTH)
    return GeneratedKey(
        prefix=stored_prefix,
        secret_hash=hashlib.sha256(secret.encode()).digest(),
        plaintext=f"{stored_prefix}_{secret}",
    )


def parse_key(raw_key: str, prefix: str) -> ParsedKey | None:
    """Split a presented credential of the kind named by `prefix`; None if it is not one."""
    if not raw_key.startswith(prefix):
        return None
    key_id, separator, secret = raw_key.removeprefix(prefix).partition("_")
    if not separator:
        return None
    if len(key_id) != _KEY_ID_LENGTH or len(secret) != _KEY_SECRET_LENGTH:
        return None
    if not set(key_id + secret) <= set(_BASE32_ALPHABET):
        return None
    return ParsedKey(prefix=prefix + key_id, secret=secret)


def key_secret_matches(secret: str, stored_hash: bytes) -> bool:
    return hmac.compare_digest(hashlib.sha256(secret.encode()).digest(), stored_hash)
