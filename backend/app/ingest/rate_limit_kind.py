"""Which capacity limit refused a call. Pure: no database, no I/O.

`classify_rate_limit` runs after `classify_error` and names the kind of a capacity refusal, so the
provider health page can tell a spend cap from a tokens-per-minute limit from an overloaded
provider, and never counts Spanlight's own per-key limiter as provider pressure.

A span is a capacity refusal when its class is `rate_limit`, when it is a `provider_5xx` that the
provider's table calls overloaded (status 529, or the type, code or message says so), or when the
gateway's own limiter refused it (`spanlight.gateway.error_code` `RATE_LIMITED`). Anything else has
no kind (`None`), and headers alone never make a refusal.

The rules, first match wins:

- G0: the gateway's own limiter refused the call: `gateway`.
- G0b: a lab fault (`spanlight.gateway.error_code` `FAULT_<SCENARIO>`) is not the provider:
  `unknown`.
- An admitted `provider_5xx` is always `overloaded` (a 529 stays `provider_5xx` and
  its kind is `overloaded`). Its message may still name tokens or requests, but those kinds mean
  the caller's quota and would charge a provider outage to the caller's limit.
- Then, for `rate_limit` spans only, the provider's table, the provider name compared
  by its canonical spelling. Providers without a specific table use the generic table. Money
  rules come first because a spend-cap message can mention tokens. Header rules come last
  because compatible upstreams can report them incorrectly. The fallback kind is `unknown`.
"""

import enum
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.ingest.error_class import ErrorClass
from app.pricing.provider_alias import canonical_provider


class RateLimitKind(enum.StrEnum):
    REQUESTS = "requests"
    TOKENS = "tokens"
    SPEND_CAP = "spend_cap"
    ACCELERATION = "acceleration"
    OVERLOADED = "overloaded"
    GATEWAY = "gateway"
    UNKNOWN = "unknown"


GATEWAY_LIMITER_CODE = "RATE_LIMITED"
FAULT_CODE_PREFIX = "FAULT_"


@dataclass(frozen=True, slots=True)
class _Facts:
    """The span's values the rules read."""

    status: int | None
    error_type: str | None
    error_code: str | None
    message: str
    headers: Mapping[str, str]

    def says(self, pattern: re.Pattern[str]) -> bool:
        return pattern.search(self.message) is not None

    def header_is_zero(self, *names: str) -> bool:
        return any(self.headers.get(name, "").strip() == "0" for name in names)


_Rule = tuple[Callable[[_Facts], bool], RateLimitKind]


def _pattern(regex: str) -> re.Pattern[str]:
    return re.compile(regex, re.IGNORECASE)


# Anthropic (rows A1-A9).
_A2 = _pattern(r"usage limits?|spend limit|monthly API usage")
_A3 = _pattern(r"acceleration")
_A4 = _pattern(r"(input|output )?tokens per (minute|day)|tokens per")
_A5 = _pattern(r"requests per (minute|day)|number of requests")
_OVERLOADED = _pattern(r"overloaded")


def _anthropic_overloaded(facts: _Facts) -> bool:
    return facts.status == 529 or facts.error_type == "overloaded_error" or facts.says(_OVERLOADED)


_ANTHROPIC: tuple[_Rule, ...] = (
    (lambda f: f.error_code == "enforced_spend_limit_reached", RateLimitKind.SPEND_CAP),
    (lambda f: f.says(_A2), RateLimitKind.SPEND_CAP),
    (lambda f: f.says(_A3), RateLimitKind.ACCELERATION),
    (lambda f: f.says(_A4), RateLimitKind.TOKENS),
    (lambda f: f.says(_A5), RateLimitKind.REQUESTS),
    (_anthropic_overloaded, RateLimitKind.OVERLOADED),
    (
        lambda f: f.header_is_zero(
            "anthropic-ratelimit-tokens-remaining",
            "anthropic-ratelimit-input-tokens-remaining",
            "anthropic-ratelimit-output-tokens-remaining",
        ),
        RateLimitKind.TOKENS,
    ),
    (
        lambda f: f.header_is_zero("anthropic-ratelimit-requests-remaining"),
        RateLimitKind.REQUESTS,
    ),
)

# OpenAI and OpenAI-compatible (rows O1-O9).
_O1_CODES = frozenset(
    {
        "insufficient_quota",
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "billing_hard_limit_reached",
    }
)
_O2 = _pattern(r"exceeded your current quota|billing|spend limit|usage limit|credit")
_O3 = _pattern(r"slow down|ramp")
_O4 = _pattern(r"tokens per (min|day)|\b(?:[IO]?TPM|TPD)\b")
_O5 = _pattern(r"requests per (sec|min|day)|\b(?:RPS|RPM|RPD)\b|Rate limit reached for requests")


def _openai_overloaded(facts: _Facts) -> bool:
    return (
        facts.error_code == "server_is_overloaded"
        or facts.error_type == "service_unavailable_error"
        or facts.says(_OVERLOADED)
    )


_OPENAI: tuple[_Rule, ...] = (
    (
        lambda f: f.error_type == "insufficient_quota" or f.error_code in _O1_CODES,
        RateLimitKind.SPEND_CAP,
    ),
    (lambda f: f.says(_O2), RateLimitKind.SPEND_CAP),
    (lambda f: f.error_code == "slow_down" or f.says(_O3), RateLimitKind.ACCELERATION),
    (lambda f: f.error_type == "tokens" or f.says(_O4), RateLimitKind.TOKENS),
    (lambda f: f.error_type == "requests" or f.says(_O5), RateLimitKind.REQUESTS),
    (_openai_overloaded, RateLimitKind.OVERLOADED),
    (
        lambda f: f.header_is_zero(
            "x-ratelimit-remaining-tokens", "x-ratelimit-remaining-project-tokens"
        ),
        RateLimitKind.TOKENS,
    ),
    (lambda f: f.header_is_zero("x-ratelimit-remaining-requests"), RateLimitKind.REQUESTS),
)

# Any other or unknown provider (rows X1-X6).
_X1 = _pattern(r"quota|billing|spend|credit|usage limit")
_X2 = _pattern(r"acceleration|slow down")
_X3 = _pattern(r"token")
_X4 = _pattern(r"request")


def _generic_overloaded(facts: _Facts) -> bool:
    return facts.status == 529 or facts.says(_OVERLOADED)


_GENERIC: tuple[_Rule, ...] = (
    (lambda f: f.says(_X1), RateLimitKind.SPEND_CAP),
    (lambda f: f.says(_X2), RateLimitKind.ACCELERATION),
    (lambda f: f.says(_X3), RateLimitKind.TOKENS),
    (lambda f: f.says(_X4), RateLimitKind.REQUESTS),
    (_generic_overloaded, RateLimitKind.OVERLOADED),
)

# provider (lowercased) -> (its rules, its "overloaded" condition)
_TABLES: Mapping[str, tuple[tuple[_Rule, ...], Callable[[_Facts], bool]]] = {
    "anthropic": (_ANTHROPIC, _anthropic_overloaded),
    "azure_openai": (_OPENAI, _openai_overloaded),
    "groq": (_OPENAI, _openai_overloaded),
    "mistral": ((*_OPENAI, *_GENERIC), _openai_overloaded),
    "xai": (_OPENAI, _openai_overloaded),
    "together": (
        ((lambda f: f.status == 429, RateLimitKind.REQUESTS), *_GENERIC),
        lambda f: f.status == 503 or _generic_overloaded(f),
    ),
    "deepseek": (
        (
            (lambda f: f.status == 402, RateLimitKind.SPEND_CAP),
            (lambda f: f.status == 429, RateLimitKind.REQUESTS),
            *_GENERIC,
        ),
        _generic_overloaded,
    ),
    "openai": (_OPENAI, _openai_overloaded),
    "google": (_OPENAI, _openai_overloaded),
    "openai_compatible": (_OPENAI, _openai_overloaded),
}
_GENERIC_TABLE = (_GENERIC, _generic_overloaded)


def classify_rate_limit(
    *,
    provider: str | None,
    status: int | None,
    error_class: ErrorClass | None,
    error_type: str | None,
    error_code: str | None,
    message: str | None,
    headers: Mapping[str, str],
    gateway_error_code: str | None,
) -> RateLimitKind | None:
    """The kind of capacity refusal, or None when the span is not one (module docstring)."""
    rules, overloaded = _TABLES.get(canonical_provider(provider) or "", _GENERIC_TABLE)
    facts = _Facts(status, error_type, error_code, message or "", headers)
    refused = (
        error_class == ErrorClass.RATE_LIMIT
        or (error_class == ErrorClass.PROVIDER_5XX and overloaded(facts))
        or gateway_error_code == GATEWAY_LIMITER_CODE
    )
    if not refused:
        return None
    if gateway_error_code == GATEWAY_LIMITER_CODE:
        return RateLimitKind.GATEWAY
    if gateway_error_code is not None and gateway_error_code.startswith(FAULT_CODE_PREFIX):
        return RateLimitKind.UNKNOWN
    if error_class == ErrorClass.PROVIDER_5XX:
        return RateLimitKind.OVERLOADED
    for matches, kind in rules:
        if matches(facts):
            return kind
    return RateLimitKind.UNKNOWN
