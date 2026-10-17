"""The SDK's rate-limit header allowlist is the server's, name for name.

The server filters ``provider.ratelimit`` again at ingest, so a header the SDK keeps but the
server does not would be dropped silently, and one the server keeps but the SDK does not would
never arrive. The backend module is parsed with ``ast`` (never imported), so this test needs no
backend dependencies; it is skipped when the SDK is tested outside the monorepo.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from spanlight import _ratelimit_headers
from spanlight._ratelimit_headers import RATELIMIT_HEADERS, snapshot_headers

# Lowercases to an ASCII "k"; a name holding it must be skipped, not matched.
KELVIN_SIGN = "\N{KELVIN SIGN}"
BACKEND_MODULE = (
    Path(__file__).resolve().parents[3] / "backend" / "app" / "ingest" / "ratelimit_headers.py"
)


def _backend_constant(name: str) -> Any:
    """The literal value the backend module assigns to ``name``."""
    if not BACKEND_MODULE.is_file():
        pytest.skip("backend tree not present")
    tree = ast.parse(BACKEND_MODULE.read_text(encoding="utf-8"))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            assert node.value is not None
            return _literal(node.value)
    raise AssertionError(f"{name} not found in {BACKEND_MODULE}")


def _literal(value: ast.expr) -> Any:
    """``frozenset({...})`` evaluates to its set; any other literal as itself."""
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        assert value.func.id == "frozenset" and len(value.args) == 1
        return frozenset(ast.literal_eval(value.args[0]))
    return ast.literal_eval(value)


def test_sdk_allowlist_equals_backend_allowlist():
    backend = _backend_constant("RATELIMIT_HEADERS")

    assert len(backend) == 29
    assert backend == RATELIMIT_HEADERS


@pytest.mark.parametrize(
    "name", ["MAX_HEADER_VALUE_CHARS", "SUCCESS_HEADER_SUFFIXES", "SUCCESS_HEADER_PREFIXES"]
)
def test_sdk_filter_constants_equal_backend_constants(name):
    assert _backend_constant(name) == getattr(_ratelimit_headers, name)


def test_credentials_are_never_allowlisted():
    for name in ("authorization", "x-api-key", "cookie", "set-cookie"):
        assert name not in RATELIMIT_HEADERS


def test_snapshot_is_a_failed_snapshot_by_default():
    headers = {"retry-after": "2", "X-RateLimit-Reset-Tokens": "1s", "x-other": "1"}

    assert snapshot_headers(headers) == {"retry-after": "2", "x-ratelimit-reset-tokens": "1s"}


def test_success_snapshot_keeps_headroom_headers_only():
    headers = {
        "retry-after": "2",
        "x-ratelimit-limit-tokens": "200000",
        "x-ratelimit-remaining-requests": "499",
        "x-ratelimit-reset-requests": "120ms",
        "anthropic-ratelimit-tokens-limit": "80000",
        "anthropic-ratelimit-tokens-remaining": "79000",
        "anthropic-ratelimit-tokens-reset": "2026-10-16T00:00:00Z",
    }

    assert snapshot_headers(headers, failed=False) == {
        "x-ratelimit-limit-tokens": "200000",
        "x-ratelimit-remaining-requests": "499",
        "anthropic-ratelimit-tokens-limit": "80000",
        "anthropic-ratelimit-tokens-remaining": "79000",
    }


def test_values_must_be_short_printable_ascii_strings():
    headers: dict[str, Any] = {
        "retry-after": "x" * 64,
        "retry-after-ms": "x" * 65,
        "x-ratelimit-limit-tokens": "café",
        "x-ratelimit-limit-requests": 500,
        f"{KELVIN_SIGN}-ratelimit-reset-tokens": "1s",
    }

    assert snapshot_headers(headers) == {"retry-after": "x" * 64}
