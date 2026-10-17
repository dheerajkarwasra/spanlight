"""The routing plan: which targets can serve a call, in what order, and what to do after a failure.

Pure: no database, no I/O, no clock. The caller owns the attempt loop and the time budget; this
module only answers "which target next" and "retry, fall back or give up". Retries and fallbacks
happen only before the first byte reaches the client, which is the caller's concern too.
"""

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.db.models.gateway import ProviderCredential, ProviderKind
from app.gateway.errors import Surface, no_compatible_target
from app.gateway.route_config import FallbackCondition, RouteConfig, Target

# A retry must leave this much of the budget for the attempt itself.
_RETRY_HEADROOM_S = 1.0

_SERVED_BY: dict[ProviderKind, frozenset[Surface]] = {
    ProviderKind.OPENAI: frozenset({"chat_completions", "responses"}),
    ProviderKind.OPENAI_COMPATIBLE: frozenset({"chat_completions", "responses"}),
    ProviderKind.ANTHROPIC: frozenset({"messages"}),
}


@dataclass(frozen=True)
class AttemptOutcome:
    """How one upstream attempt ended, reduced to what the plan needs."""

    status: int | None
    error_kind: Literal["status", "timeout", "connection_error", "blocked"]
    retry_after_s: float | None = None


@dataclass(frozen=True)
class Retry:
    """Try the same target again after waiting `after_s` seconds."""

    after_s: float


@dataclass(frozen=True)
class Fallback:
    """Move on to the route's next target."""


@dataclass(frozen=True)
class GiveUp:
    """Stop and return the last failure to the client."""


def compatible_targets(
    route: RouteConfig,
    surface: Surface,
    credentials: Mapping[UUID, ProviderCredential],
    route_name: str = "",
) -> list[int]:
    """Indices of the targets that can serve `surface`, in config order.

    There is no translation between provider formats, so an OpenAI-shaped surface only reaches
    OpenAI-style credentials and `messages` only reaches Anthropic ones. `models` is listed per
    envelope by the caller, so every target counts. A target whose credential is missing from
    `credentials` is skipped.
    """
    indices = [
        index
        for index, target in enumerate(route.targets)
        if (credential := credentials.get(target.credential_id)) is not None
        and (surface == "models" or surface in _SERVED_BY[credential.provider])
    ]
    if not indices:
        raise no_compatible_target(route_name, surface)
    return indices


def order_targets(indices: list[int], weights: list[int], rng: random.Random) -> list[int]:
    """The first target by weighted random pick, then the rest in config order.

    `weights[i]` is the weight of `indices[i]`.
    """
    first = rng.choices(indices, weights=weights, k=1)[0]
    return [first, *(index for index in indices if index != first)]


def resolve_model(target: Target, requested: str) -> str:
    """The model name to send upstream: the target's alias for it, else the name unchanged."""
    return target.model_aliases.get(requested, requested)


def attempt_timeout(remaining_s: float) -> float:
    """The time one attempt may take: whatever is left of the route's budget, never negative."""
    return max(0.0, remaining_s)


def next_action(
    route: RouteConfig,
    outcome: AttemptOutcome,
    attempts_on_target: int,
    targets_left: int,
    remaining_s: float,
) -> Retry | Fallback | GiveUp:
    """Decide what follows a failed attempt.

    `attempts_on_target` counts the attempts made on the current target, this one included;
    `targets_left` counts the targets after it. A blocked address never retries or falls back.
    """
    if outcome.error_kind == "blocked":
        return GiveUp()

    policy = route.retry
    # Timeouts and connection errors are transient, so they retry whenever attempts remain; a
    # status retries only when the route lists it.
    retryable = outcome.error_kind != "status" or outcome.status in policy.on_statuses
    if retryable and attempts_on_target < policy.max_attempts:
        wait = _wait_s(route, outcome, attempts_on_target)
        if wait <= remaining_s - _RETRY_HEADROOM_S:
            return Retry(wait)

    condition = _condition(outcome)
    if condition is not None and condition in route.fallback.on and targets_left > 0:
        return Fallback()
    return GiveUp()


def _wait_s(route: RouteConfig, outcome: AttemptOutcome, retry_number: int) -> float:
    policy = route.retry
    if policy.honour_retry_after and outcome.retry_after_s is not None:
        return max(0.0, outcome.retry_after_s)
    backoff_ms = min(policy.backoff_ms * (1 << (retry_number - 1)), policy.max_backoff_ms)
    return backoff_ms / 1000


def _condition(outcome: AttemptOutcome) -> FallbackCondition | None:
    """The fallback condition a failure belongs to; other client errors have none."""
    if outcome.error_kind == "timeout":
        return FallbackCondition.TIMEOUT
    if outcome.error_kind == "connection_error":
        return FallbackCondition.CONNECTION_ERROR
    if outcome.status == 429:
        return FallbackCondition.RATE_LIMITED
    if outcome.status in (401, 403):
        return FallbackCondition.AUTH_ERROR
    if outcome.status is not None and 500 <= outcome.status <= 599:
        return FallbackCondition.STATUS_5XX
    return None
