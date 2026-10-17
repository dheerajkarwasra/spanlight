"""Anthropic Admin API: the cost report, then the usage report, as mapped lines (§4.1, §4.2).

The cost report is grouped by `description` and `workspace_id`, which is what fills `model`,
`token_type` and `service_tier` on its results; the usage report by `model`, `service_tier`,
`context_window` and `workspace_id`, the key its counts join the cost lines on (§7.4). Both
use daily buckets, 31 per page, over `[day_from, day_to]` as whole UTC days.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.db.models.billing import BillingSourceKind
from app.reconciliation.anthropic_lines import (
    map_anthropic_cost_result,
    map_anthropic_usage_result,
)
from app.reconciliation.fetchers.base import (
    USER_AGENT,
    FetchResult,
    PageRequest,
    Query,
    bucket_results,
    check_counts,
    check_window,
    in_window,
    mapping_errors,
)
from app.reconciliation.fetchers.pages import PageReader
from app.reconciliation.line_items import InvalidLineError
from app.reconciliation.types import MappedLine, UsageBucketResult
from app.reconciliation.usage_merge import merge_usage_counts

COST_PATH = "/v1/organizations/cost_report"
USAGE_PATH = "/v1/organizations/usage_report/messages"
ANTHROPIC_VERSION = "2023-06-01"
BUCKETS_PER_PAGE = "31"

_COST_GROUPS: Query = [("group_by[]", "description"), ("group_by[]", "workspace_id")]
_USAGE_GROUPS: Query = [
    ("group_by[]", "model"),
    ("group_by[]", "service_tier"),
    ("group_by[]", "context_window"),
    ("group_by[]", "workspace_id"),
]


def headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "user-agent": USER_AGENT,
        "accept": "application/json",
    }


def window(day_from: date, day_to: date) -> Query:
    """Daily buckets from `day_from` to the end of `day_to` (`ending_at` is exclusive)."""
    return [
        ("starting_at", _rfc3339(day_from)),
        ("ending_at", _rfc3339(day_to + timedelta(days=1))),
        ("bucket_width", "1d"),
    ]


def probe_request(api_key: str, day: date) -> PageRequest:
    """One bucket of the cost report for `day`: enough to prove the key reads costs."""
    return PageRequest(COST_PATH, [*window(day, day), ("limit", "1")], headers(api_key))


class AnthropicFetcher:
    kind: BillingSourceKind = "anthropic_admin_api"

    async def fetch(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        day_from: date,
        day_to: date,
        provider_scope: str | None,
        patterns: Sequence[str],
    ) -> FetchResult:
        check_window(day_from, day_to)
        reader = PageReader(http, self.kind, headers(api_key))
        span = [*window(day_from, day_to), ("limit", BUCKETS_PER_PAGE)]
        with mapping_errors(self.kind):
            costs = await reader.buckets(COST_PATH, [*span, *_COST_GROUPS])
            lines = _cost_lines(costs, provider_scope, patterns)
            usage = _usage(
                await reader.buckets(USAGE_PATH, [*span, *_USAGE_GROUPS]), provider_scope
            )
            merged = merge_usage_counts(lines, usage, patterns)
            kept = in_window(merged, day_from, day_to)
        return FetchResult(kept, reader.pages)


def _cost_lines(
    buckets: Sequence[Mapping[str, Any]], scope: str | None, patterns: Sequence[str]
) -> list[MappedLine]:
    lines: list[MappedLine] = []
    for day, result in bucket_results(buckets, _bucket_day):
        line = map_anthropic_cost_result(day, result, scope, patterns)
        if line is not None:
            lines.append(line)
    return lines


def _usage(buckets: Sequence[Mapping[str, Any]], scope: str | None) -> list[UsageBucketResult]:
    usage: list[UsageBucketResult] = []
    for day, result in bucket_results(buckets, _bucket_day):
        mapped = map_anthropic_usage_result(day, result, scope)
        if mapped is not None:
            usage.append(check_counts(mapped))
    return usage


def _bucket_day(bucket: Mapping[str, Any]) -> date:
    """The UTC day a bucket starts on, from its RFC 3339 `starting_at`."""
    value = bucket.get("starting_at")
    if not isinstance(value, str):
        raise InvalidLineError("starting_at must be a string", column="starting_at")
    try:
        start = datetime.fromisoformat(value)
    except ValueError:
        raise InvalidLineError("starting_at is not a timestamp", column="starting_at") from None
    if start.tzinfo is None:
        raise InvalidLineError("starting_at has no offset", column="starting_at")
    return start.astimezone(UTC).date()


def _rfc3339(day: date) -> str:
    return f"{day.isoformat()}T00:00:00Z"
