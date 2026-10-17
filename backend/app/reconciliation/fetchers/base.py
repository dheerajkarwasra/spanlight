"""What the provider fetchers share: hosts, limits, the failure type, shape checks (§10.2).

Hosts are constants of the source kind; nothing a user stores can change them. The limits are
read by `pages.PageReader` at call time: `PAGE_TIMEOUT_S` per page, `MAX_RESPONSE_BYTES` per
response, `MAX_PAGES` per run.

A failure is a `FetchFailed` whose `status_line` is an HTTP status line or one of the fixed
phrases of the Global Constraints, and whose `retry` tells the job what to do with it. A count
the `bigint` columns cannot hold, reported or summed, is an invalid response; a line of a day
outside the requested window is dropped (`in_window`), so a fetch never re-points a line of
another day to a run that does not cover it. Neither
the admin key nor a response body ever reaches an exception message or a log line: a body can
echo request headers, and so the key. An invalid page is logged with the offending field's
name only.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal, Protocol

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from app.db.models.billing import BillingSourceKind
from app.reconciliation.line_items import InvalidLineError, UnsupportedCurrencyError
from app.reconciliation.types import MAX_QUANTITY, MappedLine, UsageBucketResult

logger = structlog.get_logger(__name__)


def _package_version() -> str:
    try:
        return version("spanlight-backend")
    except PackageNotFoundError:
        return "unknown"


__version__ = _package_version()

HOSTS: Mapping[BillingSourceKind, str] = {
    "anthropic_admin_api": "https://api.anthropic.com",
    "openai_costs_api": "https://api.openai.com",
}
PAGE_TIMEOUT_S = 30.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PAGES = 40
USER_AGENT = f"spanlight/{__version__} (https://spanlight.dev)"

RetryKind = Literal["none", "rate_limited", "transient"]
Query = Sequence[tuple[str, str]]


class FetchFailed(Exception):  # noqa: N818 - reads as the outcome, like the crypto errors
    """A fetch that cannot complete; `status_line` is what `last_error` stores."""

    def __init__(
        self, status_line: str, *, retry: RetryKind, retry_after_s: float | None = None
    ) -> None:
        super().__init__(status_line)
        self.status_line = status_line
        self.retry = retry
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class FetchResult:
    lines: list[MappedLine]
    pages: int


class Fetcher(Protocol):
    """One provider's cost and usage reports for `[day_from, day_to]` as mapped lines.

    `provider_scope` is the source's workspace or project filter; `patterns` are the seed and
    override model patterns lines are grouped by. Raises `FetchFailed`.
    """

    kind: BillingSourceKind

    async def fetch(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        day_from: date,
        day_to: date,
        provider_scope: str | None,
        patterns: Sequence[str],
    ) -> FetchResult: ...


def validated[M: BaseModel](model: type[M], value: object) -> M:
    """`value` as `model`, or `InvalidLineError` naming the first offending field."""
    try:
        return model.model_validate(value)
    except ValidationError as error:
        # The location only: a validation message quotes the input, which may echo the key.
        location = ".".join(str(part) for part in error.errors()[0]["loc"])
        raise InvalidLineError("unexpected shape", column=location or "body") from None


def bucket_results(
    buckets: Sequence[Mapping[str, Any]], day_of: Callable[[Mapping[str, Any]], date]
) -> Iterator[tuple[date, Mapping[str, Any]]]:
    """Every result of every bucket with the UTC day of its bucket (`day_of` checks the shape)."""
    for bucket in buckets:
        day = day_of(bucket)
        results = bucket.get("results")
        if not isinstance(results, list):
            raise InvalidLineError("results must be a list", column="results")
        for result in results:
            if not isinstance(result, Mapping):
                raise InvalidLineError("a result must be an object", column="results")
            yield day, result


def invalid_response(column: str) -> FetchFailed:
    logger.warning("billing_fetch_invalid_response", field=column)
    return FetchFailed("invalid response", retry="none")


@contextmanager
def mapping_errors(kind: BillingSourceKind) -> Iterator[None]:
    """Turn a mapper's refusal into the run's status line, logging the field name only."""
    try:
        yield
    except UnsupportedCurrencyError:
        logger.warning("billing_fetch_failed", kind=kind, status_line="unsupported currency")
        raise FetchFailed("unsupported currency", retry="none") from None
    except InvalidLineError as error:
        failure = invalid_response(error.column or "result")
        logger.warning("billing_fetch_failed", kind=kind, status_line=failure.status_line)
        raise failure from None


_COUNT_FIELDS = (
    "uncached_input",
    "cache_read",
    "cache_write_5m",
    "cache_write_1h",
    "output",
    "web_search_requests",
)


def check_counts(usage: UsageBucketResult) -> UsageBucketResult:
    """`usage` unchanged, or `InvalidLineError` when the provider reported a negative count or
    one no `bigint` column holds."""
    for name in _COUNT_FIELDS:
        count = getattr(usage, name)
        if count is not None and count < 0:
            raise InvalidLineError(f"{name} is negative", column=name)
        if count is not None and count > MAX_QUANTITY:
            raise InvalidLineError(f"{name} is too large", column=name)
    return usage


def in_window(lines: Sequence[MappedLine], day_from: date, day_to: date) -> list[MappedLine]:
    """The lines of `[day_from, day_to]`, after the usage counts were merged into them.

    A bucket of another day is dropped: the run covers only its window, so a line written under
    it for an older day would leave the run that does cover that day, and the report with it.
    Raises `InvalidLineError` for a quantity no `bigint` holds (usage summed across rows).
    """
    kept = [line for line in lines if day_from <= line.day <= day_to]
    for line in kept:
        if line.quantity is not None and line.quantity > MAX_QUANTITY:
            raise InvalidLineError("quantity is too large", column="quantity")
    if len(kept) != len(lines):
        logger.info("billing_fetch_lines_outside_window", dropped=len(lines) - len(kept))
    return kept


def day_start(day: date) -> datetime:
    """Midnight UTC at the start of `day`."""
    return datetime.combine(day, time(), UTC)


def check_window(day_from: date, day_to: date) -> None:
    if day_from > day_to:
        raise ValueError("day_from is after day_to")


@dataclass(frozen=True)
class PageRequest:
    """One GET of a provider API: path under the kind's host, query and headers."""

    path: str
    query: Query
    headers: Mapping[str, str]
