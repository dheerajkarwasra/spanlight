"""The rate-limit header allowlist and the `provider.*` attribute filter (pure)."""

import math
from typing import Any

import pytest

from app.ingest.provider_attributes import clean_provider_attributes, read_provider_fields
from app.ingest.ratelimit_headers import RATELIMIT_HEADERS, snapshot_headers

# The allowlist, written out in full: OpenAI's documented headers, the OpenAI SDKs'
# `retry-after-ms`, and Anthropic's documented response headers.
DOCUMENTED = {
    "retry-after",
    "retry-after-ms",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
    "x-ratelimit-limit-project-tokens",
    "x-ratelimit-remaining-project-tokens",
    "x-ratelimit-reset-project-tokens",
    "anthropic-ratelimit-requests-limit",
    "anthropic-ratelimit-requests-remaining",
    "anthropic-ratelimit-requests-reset",
    "anthropic-ratelimit-tokens-limit",
    "anthropic-ratelimit-tokens-remaining",
    "anthropic-ratelimit-tokens-reset",
    "anthropic-ratelimit-input-tokens-limit",
    "anthropic-ratelimit-input-tokens-remaining",
    "anthropic-ratelimit-input-tokens-reset",
    "anthropic-ratelimit-output-tokens-limit",
    "anthropic-ratelimit-output-tokens-remaining",
    "anthropic-ratelimit-output-tokens-reset",
    "anthropic-priority-input-tokens-limit",
    "anthropic-priority-input-tokens-remaining",
    "anthropic-priority-input-tokens-reset",
    "anthropic-priority-output-tokens-limit",
    "anthropic-priority-output-tokens-remaining",
    "anthropic-priority-output-tokens-reset",
}

# A failed Anthropic call: limit, remaining, reset and retry-after, with provider capitalisation.
FAILED_HEADERS = {
    "Anthropic-RateLimit-Requests-Limit": "50",
    "anthropic-ratelimit-requests-remaining": "0",
    "anthropic-ratelimit-requests-reset": "2026-10-16T07:28:00Z",
    "Retry-After": "12",
    "retry-after-ms": "11500",
    "x-ratelimit-reset-tokens": "6m0s",
}


def test_allowlist_has_the_29_documented_names() -> None:
    assert len(DOCUMENTED) == 29
    assert isinstance(RATELIMIT_HEADERS, frozenset)
    assert RATELIMIT_HEADERS == DOCUMENTED


def test_auth_and_cookie_headers_never_pass() -> None:
    headers = {
        "authorization": "Bearer sk-abc",
        "Authorization": "Bearer sk-abc",
        "x-api-key": "sk-ant-abc",
        "cookie": "session=1",
        "set-cookie": "session=1",
        "x-ratelimit-limit-requests": "500",
    }

    for failed in (True, False):
        assert snapshot_headers(headers, failed=failed) == {"x-ratelimit-limit-requests": "500"}


def test_forty_junk_headers_yield_an_empty_snapshot() -> None:
    junk = {f"x-junk-{i}": "1" for i in range(36)}
    junk |= {
        "anthropic-fast-input-tokens-limit": "1",
        "anthropic-workspace-id": "wrkspc_1",
        "anthropic-organization-id": "org_1",
        "openai-processing-ms": "120",
    }
    assert len(junk) == 40

    assert snapshot_headers(junk, failed=True) == {}


def test_a_65_char_value_is_dropped() -> None:
    headers = {"x-ratelimit-limit-tokens": "9" * 65, "x-ratelimit-limit-requests": "9" * 64}

    assert snapshot_headers(headers, failed=True) == {"x-ratelimit-limit-requests": "9" * 64}


@pytest.mark.parametrize("value", ["12\n", "1\x002", "12\x7f", "zwölf", "1\t2"])
def test_non_printable_value_is_dropped(value: str) -> None:
    assert snapshot_headers({"retry-after": value}, failed=True) == {}


def test_success_snapshot_keeps_limit_and_remaining_only() -> None:
    assert snapshot_headers(FAILED_HEADERS, failed=False) == {
        "anthropic-ratelimit-requests-limit": "50",
        "anthropic-ratelimit-requests-remaining": "0",
    }


def test_success_snapshot_keeps_openai_limit_and_remaining() -> None:
    headers = {
        "x-ratelimit-limit-requests": "500",
        "x-ratelimit-remaining-tokens": "29000",
        "x-ratelimit-remaining-project-tokens": "90000",
        "x-ratelimit-reset-requests": "120ms",
        "x-ratelimit-reset-project-tokens": "1s",
        "retry-after-ms": "120",
    }

    assert snapshot_headers(headers, failed=False) == {
        "x-ratelimit-limit-requests": "500",
        "x-ratelimit-remaining-tokens": "29000",
        "x-ratelimit-remaining-project-tokens": "90000",
    }


def test_failed_snapshot_keeps_reset_and_retry_after() -> None:
    assert snapshot_headers(FAILED_HEADERS, failed=True) == {
        "anthropic-ratelimit-requests-limit": "50",
        "anthropic-ratelimit-requests-remaining": "0",
        "anthropic-ratelimit-requests-reset": "2026-10-16T07:28:00Z",
        "retry-after": "12",
        "retry-after-ms": "11500",
        "x-ratelimit-reset-tokens": "6m0s",
    }


def test_valid_ratelimit_attribute_is_filtered_not_dropped() -> None:
    attributes = {
        "provider.ratelimit": {"Retry-After": "3", "cookie": "a=b"},
        "gen_ai.system": "anthropic",
    }

    cleaned = clean_provider_attributes(attributes)

    assert cleaned == {"provider.ratelimit": {"retry-after": "3"}, "gen_ai.system": "anthropic"}
    assert attributes["provider.ratelimit"] == {"Retry-After": "3", "cookie": "a=b"}


def test_nested_object_in_ratelimit_is_dropped() -> None:
    attributes = {
        "provider.ratelimit": {"retry-after": "3", "x-ratelimit-limit-tokens": {"a": "1"}}
    }

    assert clean_provider_attributes(attributes) == {}


@pytest.mark.parametrize(
    "value", ["retry-after: 3", ["retry-after", "3"], None, {"retry-after": 3}]
)
def test_non_object_ratelimit_is_dropped(value: Any) -> None:
    cleaned = clean_provider_attributes({"provider.ratelimit": value, "other": 1})

    assert cleaned == {"other": 1}


def test_error_type_longer_than_64_is_dropped() -> None:
    attributes = {
        "provider.error.type": "e" * 65,
        "provider.error.code": "enforced_spend_limit_reached",
        "provider.request_id": "r" * 129,
    }

    assert clean_provider_attributes(attributes) == {
        "provider.error.code": "enforced_spend_limit_reached"
    }
    assert clean_provider_attributes({"provider.error.type": 429}) == {}
    assert clean_provider_attributes({"provider.request_id": "r" * 128}) == {
        "provider.request_id": "r" * 128
    }


@pytest.mark.parametrize("value", ["2", -1, math.nan, math.inf, 10**400, True, None])
def test_retry_after_s_must_be_a_non_negative_number(value: Any) -> None:
    assert clean_provider_attributes({"provider.retry_after_s": value}) == {}
    assert clean_provider_attributes({"provider.retry_after_s": 2.5}) == {
        "provider.retry_after_s": 2.5
    }


def test_provider_fields_are_read_from_attributes() -> None:
    fields = read_provider_fields(
        {
            "provider.error.type": "rate_limit_error",
            "provider.error.code": "enforced_spend_limit_reached",
            "provider.ratelimit": {"retry-after": "3", "authorization": "Bearer x"},
            "provider.request_id": "req_1",
            "provider.retry_after_s": 3,
            "spanlight.gateway.error_code": "BUDGET_EXCEEDED",
            "spanlight.gateway.status": 429,
        }
    )

    assert fields.error_type == "rate_limit_error"
    assert fields.error_code == "enforced_spend_limit_reached"
    assert fields.ratelimit == {"retry-after": "3"}
    assert fields.request_id == "req_1"
    assert fields.retry_after_s == 3.0
    assert fields.gateway_error_code == "BUDGET_EXCEEDED"
    assert fields.gateway_status == 429


def test_missing_or_malformed_provider_fields_read_as_unknown() -> None:
    fields = read_provider_fields(
        {
            "provider.error.type": ["rate_limit_error"],
            "provider.ratelimit": "retry-after: 3",
            "provider.retry_after_s": -1,
            "spanlight.gateway.error_code": 7,
            "spanlight.gateway.status": "429",
        }
    )

    assert fields.error_type is None
    assert fields.error_code is None
    assert fields.ratelimit == {}
    assert fields.request_id is None
    assert fields.retry_after_s is None
    assert fields.gateway_error_code is None
    assert fields.gateway_status is None
    assert read_provider_fields({"spanlight.gateway.status": True}).gateway_status is None


def test_non_ascii_header_name_never_aliases_an_allowlisted_one() -> None:
    # U+212A KELVIN SIGN lowercases to an ASCII "k".
    kelvin = "x-ratelimit-limit-to" + chr(0x212A) + "ens"
    assert kelvin.lower() == "x-ratelimit-limit-tokens"

    assert snapshot_headers({kelvin: "500"}, failed=True) == {}


def test_retry_after_s_is_stored_as_the_float_it_was_read_as() -> None:
    cleaned = clean_provider_attributes({"provider.retry_after_s": 10**300})

    assert cleaned == {"provider.retry_after_s": 1e300}
    assert type(cleaned["provider.retry_after_s"]) is float
    assert (
        type(clean_provider_attributes({"provider.retry_after_s": 3})["provider.retry_after_s"])
        is float
    )


@pytest.mark.parametrize(
    ("status", "expected"), [(99, None), (100, 100), (599, 599), (600, None), (-1, None), (0, None)]
)
def test_gateway_status_outside_http_range_reads_as_unknown(
    status: int, expected: int | None
) -> None:
    assert read_provider_fields({"spanlight.gateway.status": status}).gateway_status == expected
