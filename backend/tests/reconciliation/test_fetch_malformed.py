"""A provider response as an attack vector (spec §12): huge, malformed, deep or mistyped.

Failure mode: a response that is not what the provider promises is half-mapped and written, or
crashes the worker with a stack trace in `last_error`, or a body larger than the cap is read
into memory whole. Every case here must end the fetch with one fixed status line.
"""

import gzip
import json
from typing import Any

import pytest

from app.config import Settings
from app.reconciliation.fetchers import FetchFailed, fetcher_for
from app.reconciliation.fetchers import base as fetch_base
from tests.reconciliation.fetch_support import (
    ANTHROPIC_COST_PATH,
    ANTHROPIC_KEY,
    ANTHROPIC_USAGE_PATH,
    DAY_FROM,
    DAY_TO,
    OPENAI_COST_PATH,
    OPENAI_KEY,
    OPENAI_USAGE_PATH,
    PATTERNS,
    Reply,
    ScriptedApi,
    fixture_page,
    page,
    vetted_client,
)

OVERSIZE = fetch_base.MAX_RESPONSE_BYTES + 1


def cost_result(**changes: Any) -> dict[str, Any]:
    result: dict[str, Any] = fixture_page("anthropic_cost_page.json")["data"][0]["results"][0]
    return result | changes


def usage_result(**changes: Any) -> dict[str, Any]:
    result: dict[str, Any] = fixture_page("anthropic_usage_page.json")["data"][0]["results"][0]
    return result | changes


def bucket(*results: dict[str, Any]) -> dict[str, Any]:
    return {
        "starting_at": "2026-10-01T00:00:00Z",
        "ending_at": "2026-10-02T00:00:00Z",
        "results": list(results),
    }


def nested(depth: int) -> bytes:
    return b'{"data": ' + b"[" * depth + b"]" * depth + b', "has_more": false}'


GOOD_COST = Reply(json=page([bucket(cost_result())]))
GOOD_USAGE = Reply(json=page([bucket(usage_result())]))

CASES: list[tuple[str, Reply, Reply, str]] = [
    ("oversize", Reply(body=b" " * OVERSIZE), GOOD_USAGE, "response too large"),
    (
        "oversize-chunked",
        Reply(body=b" " * OVERSIZE, chunked=True),
        GOOD_USAGE,
        "response too large",
    ),
    ("oversize-usage", GOOD_COST, Reply(body=b" " * OVERSIZE), "response too large"),
    (
        "compressed",
        Reply(
            body=gzip.compress(json.dumps(page([])).encode()),
            headers={"content-encoding": "gzip"},
        ),
        GOOD_USAGE,
        "invalid response",
    ),
    ("truncated-json", Reply(body=b'{"data": [{"starting_at": '), GOOD_USAGE, "invalid response"),
    ("nan", Reply(body=b'{"data": [], "has_more": NaN}'), GOOD_USAGE, "invalid response"),
    ("infinity", Reply(body=b'{"data": [], "has_more": Infinity}'), GOOD_USAGE, "invalid response"),
    ("nested-200-deep", Reply(body=nested(200)), GOOD_USAGE, "invalid response"),
    ("not-json", Reply(body=b"<html>bad gateway</html>"), GOOD_USAGE, "invalid response"),
    ("not-an-object", Reply(json=[1, 2]), GOOD_USAGE, "invalid response"),
    ("missing-data", Reply(json={"has_more": False}), GOOD_USAGE, "invalid response"),
    (
        "data-not-a-list",
        Reply(json={"data": {}, "has_more": False}),
        GOOD_USAGE,
        "invalid response",
    ),
    ("missing-has-more", Reply(json={"data": []}), GOOD_USAGE, "invalid response"),
    (
        "has-more-not-bool",
        Reply(json={"data": [], "has_more": "no", "next_page": None}),
        GOOD_USAGE,
        "invalid response",
    ),
    (
        "bucket-without-start",
        Reply(json=page([{"results": [cost_result()]}])),
        GOOD_USAGE,
        "invalid response",
    ),
    (
        "bucket-start-not-a-date",
        Reply(json=page([bucket(cost_result()) | {"starting_at": "yesterday"}])),
        GOOD_USAGE,
        "invalid response",
    ),
    (
        "results-not-objects",
        Reply(json=page([bucket() | {"results": ["x"]}])),
        GOOD_USAGE,
        "invalid response",
    ),
    (
        "non-string-description",
        Reply(json=page([bucket(cost_result(description=5))])),
        GOOD_USAGE,
        "invalid response",
    ),
    (
        "amount-not-a-string",
        Reply(json=page([bucket(cost_result(amount=12.5))])),
        GOOD_USAGE,
        "invalid response",
    ),
    (
        "unknown-token-type",
        Reply(json=page([bucket(cost_result(token_type="mystery_tokens"))])),
        GOOD_USAGE,
        "invalid response",
    ),
    (
        "negative-quantity",
        GOOD_COST,
        Reply(json=page([bucket(usage_result(output_tokens=-5))])),
        "invalid response",
    ),
    (
        "negative-nested-quantity",
        GOOD_COST,
        Reply(json=page([bucket(usage_result(cache_creation={"ephemeral_5m_input_tokens": -1}))])),
        "invalid response",
    ),
    (
        "huge-usage-count",
        GOOD_COST,
        Reply(json=page([bucket(usage_result(output_tokens=2**63))])),
        "invalid response",
    ),
    (
        "summed-usage-count-over-bigint",
        GOOD_COST,
        Reply(
            json=page(
                [
                    bucket(
                        usage_result(uncached_input_tokens=2**62),
                        usage_result(uncached_input_tokens=2**62),
                    )
                ]
            )
        ),
        "invalid response",
    ),
    (
        "usage-missing-data",
        GOOD_COST,
        Reply(json={"has_more": False}),
        "invalid response",
    ),
    (
        "eur",
        Reply(json=page([bucket(cost_result(currency="EUR"))])),
        GOOD_USAGE,
        "unsupported currency",
    ),
]


@pytest.mark.parametrize(
    ("cost", "usage", "expected"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
async def test_malformed_anthropic_response_fails_the_fetch(
    settings: Settings, cost: Reply, usage: Reply, expected: str
) -> None:
    api = ScriptedApi(routes={ANTHROPIC_COST_PATH: [cost], ANTHROPIC_USAGE_PATH: [usage]})
    async with vetted_client(settings, api) as http:
        with pytest.raises(FetchFailed) as raised:
            await fetcher_for("anthropic_admin_api").fetch(
                http, ANTHROPIC_KEY, DAY_FROM, DAY_TO, None, PATTERNS
            )
    assert raised.value.status_line == expected
    assert raised.value.retry == "none"


def openai_cost(**amount: Any) -> dict[str, Any]:
    return {
        "start_time": 1790812800,
        "end_time": 1790899200,
        "results": [
            {
                "amount": {"value": 0.1, "currency": "usd"} | amount,
                "line_item": "gpt-4o, input",
                "project_id": None,
            }
        ],
    }


OPENAI_CASES: list[tuple[str, bytes, str]] = [
    ("nan-constant", b'{"data": [], "has_more": false, "x": NaN}', "invalid response"),
    (
        "start-not-int",
        json.dumps(page([{"start_time": "1", "results": []}])).encode(),
        "invalid response",
    ),
    (
        "start-bool",
        json.dumps(page([{"start_time": True, "results": []}])).encode(),
        "invalid response",
    ),
    ("amount-string", json.dumps(page([openai_cost(value="0.1")])).encode(), "invalid response"),
    ("eur", json.dumps(page([openai_cost(currency="eur")])).encode(), "unsupported currency"),
    ("huge-number", json.dumps(page([openai_cost(value=10**7)])).encode(), "invalid response"),
]


@pytest.mark.parametrize(
    ("body", "expected"),
    [case[1:] for case in OPENAI_CASES],
    ids=[case[0] for case in OPENAI_CASES],
)
async def test_malformed_openai_response_fails_the_fetch(
    settings: Settings, body: bytes, expected: str
) -> None:
    api = ScriptedApi(
        routes={OPENAI_COST_PATH: [Reply(body=body)], OPENAI_USAGE_PATH: [Reply(json=page([]))]}
    )
    async with vetted_client(settings, api) as http:
        with pytest.raises(FetchFailed) as raised:
            await fetcher_for("openai_costs_api").fetch(
                http, OPENAI_KEY, DAY_FROM, DAY_TO, None, PATTERNS
            )
    assert raised.value.status_line == expected
