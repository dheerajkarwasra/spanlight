"""Fetching prompts from ``GET /v1/prompts/{name}`` with a small revalidating cache.

The cache is keyed by ``(name, label, version)``. A fresh entry is served without a request;
an expired one is revalidated with ``If-None-Match``, so an unchanged prompt costs a ``304``.
When the server cannot be reached or answers with an error, an expired entry keeps being
served (a prompt outage must not become an application outage). With nothing cached, the
caller's ``fallback`` is used; with no fallback either, :class:`PromptUnavailableError` is
raised.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from ._prompts import Prompt, PromptUnavailableError, fallback_prompt, prompt_from_wire
from ._templates import InvalidTemplateError
from ._version import __version__

logger = logging.getLogger("spanlight")

CacheKey = tuple[str, str, int | None]
Fallback = str | Sequence[Mapping[str, Any]]

_WARNING_INTERVAL = 60.0
# Seconds a failed fetch is remembered, so callers queued behind it do not each time out.
_FAILURE_MEMO = 5.0


@dataclass
class _Entry:
    prompt: Prompt
    etag: str | None
    expires_at: float


class _FetchError(Exception):
    """Why one fetch failed; the message never contains the API key or the prompt body."""


class PromptClient:
    """Reads prompts for one Spanlight client. Reach it as ``Spanlight.prompts``.

    Args:
        base_url: URL of the prompts collection, e.g. ``http://localhost:8000/v1/prompts``.
        api_key: Project API key with the ``prompts:read`` scope, or ``None``.
        timeout: Per-request timeout in seconds.
        cache_ttl: Seconds a fetched prompt is served without asking the server again.
        transport: Optional custom ``httpx`` transport.
        clock: Monotonic clock, replaceable in tests.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout: float,
        cache_ttl: float,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._ttl = cache_ttl
        self._transport = transport
        self._clock = clock
        self._cache: dict[CacheKey, _Entry] = {}
        self._locks: dict[CacheKey, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._last_warning: dict[CacheKey, float] = {}
        # Per key: until when a failed fetch is not retried, and why it failed.
        self._failures: dict[CacheKey, tuple[float, str]] = {}
        self._client: httpx.Client | None = None
        self._client_guard = threading.Lock()

    def get(
        self,
        name: str,
        label: str = "production",
        *,
        version: int | None = None,
        fallback: Fallback | None = None,
    ) -> Prompt:
        """Return a prompt version by label (default ``production``) or by number.

        Args:
            name: The prompt name, e.g. ``"support/answer"``.
            label: The label to resolve; ignored when ``version`` is given.
            version: A specific version number.
            fallback: A text template, or a list of ``{"role", "content"}`` messages, used
                when the prompt cannot be fetched and nothing is cached. The returned
                prompt then has ``version=None``.

        Returns:
            The prompt. A cached copy is returned while fresh, and also after it expires
            whenever the server cannot be reached.

        Raises:
            PromptUnavailableError: Nothing is cached, the fetch failed and there is no
                valid ``fallback``; or ``name`` is empty or has an empty, ``.`` or ``..``
                segment (no request is made).
        """
        key: CacheKey = (name, label, version)
        if not _is_valid_name(name):
            raise PromptUnavailableError(f"prompt {name!r} is unavailable: invalid prompt name")
        entry = self._cache.get(key)
        if entry is not None and self._clock() < entry.expires_at:
            return entry.prompt
        lock = self._lock_for(key)
        if not lock.acquire(blocking=False):
            if entry is not None:
                # Another caller is refreshing this prompt; serve the expired copy rather than
                # wait on the server.
                return entry.prompt
            lock.acquire()
        try:
            return self._get_locked(key, fallback)
        finally:
            lock.release()

    def close(self) -> None:
        """Close the HTTP connection pool. A later ``get`` opens a new one."""
        with self._client_guard:
            client, self._client = self._client, None
        if client is not None:
            client.close()

    def _get_locked(self, key: CacheKey, fallback: Fallback | None) -> Prompt:
        # Another thread may have refreshed the entry while this one waited.
        entry = self._cache.get(key)
        now = self._clock()
        if entry is not None and now < entry.expires_at:
            return entry.prompt
        failure = self._failures.get(key)
        if entry is None and failure is not None and now < failure[0]:
            # A fetch failed moments ago; do not make this caller wait on the server too.
            return self._without_cache(key, fallback, failure[1])
        try:
            return self._refresh(key, entry)
        except _FetchError as error:
            cause = str(error)
        except Exception as error:
            logger.debug("Unexpected error fetching prompt %r", key[0], exc_info=True)
            cause = type(error).__name__
        return self._recover(key, entry, fallback, cause)

    def _lock_for(self, key: CacheKey) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _refresh(self, key: CacheKey, entry: _Entry | None) -> Prompt:
        response = self._request(key, entry.etag if entry is not None else None)
        now = self._clock()
        if response.status_code == 304 and entry is not None:
            entry.expires_at = now + self._ttl
            return entry.prompt
        if response.status_code != 200:
            raise _FetchError(f"HTTP {response.status_code}")
        try:
            prompt = prompt_from_wire(response.json())
        except (ValueError, InvalidTemplateError) as error:
            raise _FetchError(f"unreadable response ({error})") from None
        self._cache[key] = _Entry(prompt, response.headers.get("ETag"), now + self._ttl)
        self._failures.pop(key, None)
        return prompt

    def _request(self, key: CacheKey, etag: str | None) -> httpx.Response:
        if not self._api_key:
            raise _FetchError("no API key configured")
        name, label, version = key
        params = {"version": str(version)} if version is not None else {"label": label}
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": f"spanlight-python/{__version__}",
        }
        if etag is not None:
            headers["If-None-Match"] = etag
        url = f"{self._base_url}/{quote(name, safe='/')}"
        try:
            return self._http_client().get(url, params=params, headers=headers)
        except httpx.HTTPError as error:
            raise _FetchError(type(error).__name__) from None

    def _recover(
        self, key: CacheKey, entry: _Entry | None, fallback: Fallback | None, cause: str
    ) -> Prompt:
        name = key[0]
        if entry is not None:
            # Keep serving the stale copy and wait before asking again, so an outage does not
            # put a request (and its timeout) on every call.
            entry.expires_at = self._clock() + max(self._ttl, _FAILURE_MEMO)
            self._warn(key, f"Spanlight: serving a cached copy of prompt {name!r} ({cause})")
            return entry.prompt
        # Remember the failure: briefly when the caller has nothing else (so concurrent callers
        # do not each wait out a timeout), one TTL when a fallback covers the gap.
        window = _FAILURE_MEMO if fallback is None else max(self._ttl, _FAILURE_MEMO)
        self._failures[key] = (self._clock() + window, cause)
        return self._without_cache(key, fallback, cause)

    def _without_cache(self, key: CacheKey, fallback: Fallback | None, cause: str) -> Prompt:
        """The fallback prompt, or :class:`PromptUnavailableError` when there is none."""
        name = key[0]
        if fallback is None:
            raise PromptUnavailableError(f"prompt {name!r} is unavailable: {cause}")
        try:
            prompt = fallback_prompt(name, fallback)
        except (InvalidTemplateError, TypeError) as error:
            raise PromptUnavailableError(
                f"prompt {name!r} is unavailable ({cause}) and its fallback is invalid: {error}"
            ) from None
        self._warn(key, f"Spanlight: using the fallback for prompt {name!r} ({cause})")
        return prompt

    def _warn(self, key: CacheKey, message: str) -> None:
        now = self._clock()
        last = self._last_warning.get(key)
        if last is None or now - last >= _WARNING_INTERVAL:
            self._last_warning[key] = now
            logger.warning(message)

    def _http_client(self) -> httpx.Client:
        with self._client_guard:
            if self._client is None:
                self._client = httpx.Client(transport=self._transport, timeout=self._timeout)
            return self._client


def _is_valid_name(name: str) -> bool:
    """Whether ``name`` can travel in the URL path: no empty, ``.`` or ``..`` segments."""
    if not isinstance(name, str) or not name:
        return False
    return all(segment not in ("", ".", "..") for segment in name.split("/"))
