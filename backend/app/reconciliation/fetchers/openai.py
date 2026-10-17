"""OpenAI Costs API, then the completions usage API, as mapped lines (spec §4.3).

The costs are grouped by `line_item` and `project_id`; the usage by `model`, `batch` and
`project_id`, the key its counts join the cost lines on (§7.4). Both use daily buckets, 31 per
page, over `[day_from, day_to]` as whole UTC days given in Unix seconds (`end_time` exclusive).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.db.models.billing import BillingSourceKind
from app.reconciliation.fetchers.base import (
    USER_AGENT,
    FetchResult,
    PageRequest,
    Query,
    bucket_results,
    check_counts,
    check_window,
    day_start,
    in_window,
    mapping_errors,
)
from app.reconciliation.fetchers.pages import PageReader
from app.reconciliation.line_items import InvalidLineError
from app.reconciliation.openai_lines import map_openai_cost_result, map_openai_usage_result
from app.reconciliation.types import MappedLine, UsageBucketResult
from app.reconciliation.usage_merge import merge_usage_counts

COST_PATH = "/v1/organization/costs"
USAGE_PATH = "/v1/organization/usage/completions"
BUCKETS_PER_PAGE = "31"

_COST_GROUPS: Query = [("group_by[]", "line_item"), ("group_by[]", "project_id")]
_USAGE_GROUPS: Query = [
    ("group_by[]", "model"),
    ("group_by[]", "batch"),
    ("group_by[]", "project_id"),
]


def headers(api_key: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {api_key}",
        "user-agent": USER_AGENT,
        "accept": "application/json",
    }


def window(day_from: date, day_to: date) -> Query:
    """Daily buckets from `day_from` to the end of `day_to`."""
    return [
        ("start_time", _unix(day_from)),
        ("end_time", _unix(day_to + timedelta(days=1))),
        ("bucket_width", "1d"),
    ]


def probe_request(api_key: str, day: date) -> PageRequest:
    """One bucket of the costs for `day`: enough to prove the key reads costs."""
    return PageRequest(COST_PATH, [*window(day, day), ("limit", "1")], headers(api_key))


class OpenAIFetcher:
    kind: BillingSourceKind = "openai_costs_api"

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
        line = map_openai_cost_result(day, result, scope, patterns)
        if line is not None:
            lines.append(line)
    return lines


def _usage(buckets: Sequence[Mapping[str, Any]], scope: str | None) -> list[UsageBucketResult]:
    usage: list[UsageBucketResult] = []
    for day, result in bucket_results(buckets, _bucket_day):
        mapped = map_openai_usage_result(day, result, scope)
        if mapped is not None:
            usage.append(check_counts(mapped))
    return usage


def _bucket_day(bucket: Mapping[str, Any]) -> date:
    """The UTC day a bucket starts on, from its `start_time` in Unix seconds."""
    value = bucket.get("start_time")
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidLineError("start_time must be an integer", column="start_time")
    try:
        return datetime.fromtimestamp(value, UTC).date()
    except (OverflowError, OSError, ValueError):
        raise InvalidLineError("start_time is out of range", column="start_time") from None


def _unix(day: date) -> str:
    return str(int(day_start(day).timestamp()))
