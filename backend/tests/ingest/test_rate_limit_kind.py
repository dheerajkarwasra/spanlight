"""The rate-limit kind classifier (pure): its rule table, one case per row."""

from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from app.db.models import SpanStatus
from app.ingest.error_class import ErrorClass, classify_error, numeric_status
from app.ingest.rate_limit_kind import RateLimitKind, classify_rate_limit


@dataclass(frozen=True)
class Case:
    """One failed (or successful) span, as the classifier sees it."""

    provider: str | None
    status: int | None
    error_type: str | None = None
    error_code: str | None = None
    message: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    gateway_error_code: str | None = None
    ok: bool = False


def _classify(case: Case) -> tuple[ErrorClass | None, RateLimitKind | None]:
    """Run both classifiers the way ingest does: the class first, then the kind."""
    attributes: dict[str, object] = {}
    if case.status is not None:
        attributes["spanlight.gateway.upstream_status"] = case.status
    if case.gateway_error_code is not None:
        attributes["spanlight.gateway.error_code"] = case.gateway_error_code
    if case.error_type is not None:
        attributes["provider.error.type"] = case.error_type
    span_status = SpanStatus.OK if case.ok else SpanStatus.ERROR
    error_class = classify_error(span_status, case.message, attributes)
    kind = classify_rate_limit(
        provider=case.provider,
        status=numeric_status(attributes),
        error_class=error_class,
        error_type=case.error_type,
        error_code=case.error_code,
        message=case.message,
        headers=case.headers,
        gateway_error_code=case.gateway_error_code,
    )
    return error_class, kind


# (case, expected class or None when the row does not pin one, expected kind)
CASES = [
    pytest.param(
        Case("anthropic", 429, "rate_limit_error", "enforced_spend_limit_reached", headers={}),
        None,
        RateLimitKind.SPEND_CAP,
        id="anthropic spend cap",
    ),
    pytest.param(
        Case(
            "anthropic",
            429,
            message=(
                "You have reached your API usage limits: your organization has crossed its "
                "monthly API usage threshold…"
            ),
        ),
        None,
        RateLimitKind.SPEND_CAP,
        id="anthropic spend cap, code missing",
    ),
    pytest.param(
        Case(
            "anthropic",
            400,
            "invalid_request_error",
            message="You have reached your specified API usage limits…",
        ),
        ErrorClass.RATE_LIMIT,
        RateLimitKind.SPEND_CAP,
        id="anthropic self-set limit",
    ),
    pytest.param(
        Case(
            "anthropic", 400, message="You have reached your specified workspace API usage limits…"
        ),
        None,
        RateLimitKind.SPEND_CAP,
        id="anthropic workspace limit",
    ),
    pytest.param(
        Case("anthropic", 429, message="…acceleration limit…"),
        None,
        RateLimitKind.ACCELERATION,
        id="anthropic acceleration",
    ),
    pytest.param(
        Case(
            "anthropic",
            429,
            message=(
                "This request would exceed the rate limit for your organization … of 400,000 "
                "output tokens per minute"
            ),
        ),
        None,
        RateLimitKind.TOKENS,
        id="anthropic OTPM",
    ),
    pytest.param(
        Case("anthropic", 429, message="… input tokens per minute"),
        None,
        RateLimitKind.TOKENS,
        id="anthropic ITPM",
    ),
    pytest.param(
        Case("anthropic", 429, message="… 1,000 requests per minute"),
        None,
        RateLimitKind.REQUESTS,
        id="anthropic RPM",
    ),
    pytest.param(
        Case("anthropic", 529, "overloaded_error", message="Overloaded"),
        ErrorClass.PROVIDER_5XX,
        RateLimitKind.OVERLOADED,
        id="anthropic 529",
    ),
    pytest.param(
        Case(
            "anthropic",
            429,
            message="",
            headers={"anthropic-ratelimit-output-tokens-remaining": "0"},
        ),
        None,
        RateLimitKind.TOKENS,
        id="anthropic headers only",
    ),
    pytest.param(
        Case("anthropic", 429, message="", headers={"anthropic-ratelimit-requests-remaining": "0"}),
        None,
        RateLimitKind.REQUESTS,
        id="anthropic headers only, requests",
    ),
    pytest.param(
        Case("anthropic", 429, message="Rate limited", headers={}),
        None,
        RateLimitKind.UNKNOWN,
        id="anthropic unknown",
    ),
    pytest.param(
        Case(
            "openai",
            429,
            "insufficient_quota",
            "insufficient_quota",
            message="You exceeded your current quota…",
        ),
        None,
        RateLimitKind.SPEND_CAP,
        id="openai insufficient_quota",
    ),
    pytest.param(
        Case("openai", 429, error_code="credit_balance_exhausted"),
        None,
        RateLimitKind.SPEND_CAP,
        id="openai credit balance",
    ),
    pytest.param(
        Case("openai", 429, error_code="project_spend_limit_exceeded"),
        None,
        RateLimitKind.SPEND_CAP,
        id="openai project spend limit",
    ),
    pytest.param(
        Case("openai", 429, "rate_limit_error", "slow_down"),
        None,
        RateLimitKind.ACCELERATION,
        id="openai slow_down",
    ),
    pytest.param(
        Case(
            "openai",
            429,
            "tokens",
            "rate_limit_exceeded",
            message="Rate limit reached for gpt-4.1 … on tokens per min (TPM)…",
        ),
        None,
        RateLimitKind.TOKENS,
        id="openai tokens",
    ),
    pytest.param(
        Case("openai", 429, "requests", "rate_limit_exceeded", "Rate limit reached for requests"),
        None,
        RateLimitKind.REQUESTS,
        id="openai requests",
    ),
    pytest.param(
        Case("openai", 503, "service_unavailable_error", "server_is_overloaded"),
        ErrorClass.PROVIDER_5XX,
        RateLimitKind.OVERLOADED,
        id="openai overloaded",
    ),
    pytest.param(
        Case("openai", 429, message="", headers={"x-ratelimit-remaining-tokens": "0"}),
        None,
        RateLimitKind.TOKENS,
        id="openai header fallback",
    ),
    pytest.param(
        Case("openai_compatible", 429, "tokens"),
        None,
        RateLimitKind.TOKENS,
        id="openai_compatible uses openai table",
    ),
    pytest.param(
        Case(
            "openai",
            429,
            "requests",
            "rate_limit_exceeded",
            "Rate limit reached for this gateway key: 60 requests per minute.",
            gateway_error_code="RATE_LIMITED",
        ),
        ErrorClass.RATE_LIMIT,
        RateLimitKind.GATEWAY,
        id="gateway key limit",
    ),
    # A lab fault carries `spanlight.gateway.error_code = FAULT_<SCENARIO>`, and its
    # OpenAI-shaped message would otherwise match O5.
    pytest.param(
        Case(
            "openai",
            429,
            "requests",
            "rate_limit_exceeded",
            "Rate limit reached for requests.",
            gateway_error_code="FAULT_RATE_LIMITED",
        ),
        None,
        RateLimitKind.UNKNOWN,
        id="gateway rate_limited fault",
    ),
    pytest.param(Case(None, 429, message=""), None, RateLimitKind.UNKNOWN, id="OTLP, status only"),
    pytest.param(
        Case(None, 429, message="quota exceeded"),
        None,
        RateLimitKind.SPEND_CAP,
        id="OTLP, message only",
    ),
    pytest.param(
        Case("anthropic", 500, "api_error", message="Internal server error"),
        None,
        None,
        id="not a refusal",
    ),
    pytest.param(Case("openai", None, ok=True), None, None, id="success"),
    pytest.param(
        Case("openai", None, headers={"x-ratelimit-remaining-tokens": "0"}, ok=True),
        None,
        None,
        id="spoofed header on success",
    ),
]


@pytest.mark.parametrize(("case", "expected_class", "expected_kind"), CASES)
def test_spec_table(
    case: Case, expected_class: ErrorClass | None, expected_kind: RateLimitKind | None
) -> None:
    error_class, kind = _classify(case)

    if expected_class is not None:
        assert error_class == expected_class
    assert kind == expected_kind


# Anthropic's self-set limit as Spanlight's own capture paths word it: the gateway's status
# message (`<status> <error type>: <message>`) and the SDK's `str(anthropic.APIStatusError)`.
_SELF_SET = (
    "You have reached your specified API usage limits. You will regain access on 2026-11-01."
)
_GATEWAY_SELF_SET = f"400 invalid_request_error: {_SELF_SET}"
_SDK_SELF_SET = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    f"'message': '{_SELF_SET}'}}}}"
)
_SDK_WORKSPACE_SELF_SET = _SDK_SELF_SET.replace("specified API", "specified workspace API")


@pytest.mark.parametrize("message", [_GATEWAY_SELF_SET, _SDK_SELF_SET, _SDK_WORKSPACE_SELF_SET])
def test_self_set_limit_as_the_gateway_and_sdk_word_it_is_a_spend_cap(message: str) -> None:
    case = Case("anthropic", 400, "invalid_request_error", message=message)

    assert _classify(case) == (ErrorClass.RATE_LIMIT, RateLimitKind.SPEND_CAP)


def test_self_set_limit_phrase_inside_a_400_without_its_error_type_stays_client() -> None:
    case = Case("anthropic", 400, message=_GATEWAY_SELF_SET)

    assert _classify(case) == (ErrorClass.CLIENT, None)
    assert _classify(Case("anthropic", 400, "not_found_error", message=_SDK_SELF_SET)) == (
        ErrorClass.CLIENT,
        None,
    )


def test_spec_table_has_every_row() -> None:
    assert len(CASES) == 28


def test_kind_values_match_the_column_check() -> None:
    assert {kind.value for kind in RateLimitKind} == {
        "requests",
        "tokens",
        "spend_cap",
        "acceleration",
        "overloaded",
        "gateway",
        "unknown",
    }


def test_provider_name_is_matched_case_insensitively() -> None:
    case = Case("Anthropic", 429, message="… 400,000 output tokens per minute")

    assert _classify(case) == (ErrorClass.RATE_LIMIT, RateLimitKind.TOKENS)
    assert _classify(Case("OpenAI", 429, "tokens"))[1] == RateLimitKind.TOKENS


def test_unknown_provider_uses_generic_table() -> None:
    message = "token limit hit"

    assert _classify(Case("mistral", 429, message=message))[1] == RateLimitKind.TOKENS
    # The Anthropic table has no bare "token" rule, so the same span there is unknown.
    assert _classify(Case("anthropic", 429, message=message))[1] == RateLimitKind.UNKNOWN


def test_rules_are_tried_in_order() -> None:
    case = Case(
        "anthropic", 429, message="Your spend limit is reached; also 50,000 tokens per minute."
    )

    assert _classify(case)[1] == RateLimitKind.SPEND_CAP


def test_server_error_that_is_not_overloaded_has_no_kind() -> None:
    case = Case("openai", 503, "server_error", message="The server had an error")

    assert _classify(case) == (ErrorClass.PROVIDER_5XX, None)


def test_headers_never_make_a_server_error_a_refusal() -> None:
    case = Case("openai", 500, headers={"x-ratelimit-remaining-tokens": "0"})

    assert _classify(case) == (ErrorClass.PROVIDER_5XX, None)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            Case(None, 529, message="Server overloaded, please retry your request"),
            id="generic 529 naming a request",
        ),
        pytest.param(
            Case(
                "anthropic",
                529,
                "overloaded_error",
                message="Overloaded while serving 400,000 output tokens per minute",
            ),
            id="anthropic 529 naming tokens per minute",
        ),
        pytest.param(
            Case(
                "openai",
                503,
                "service_unavailable_error",
                "server_is_overloaded",
                "The server is overloaded; please ramp down your traffic",
            ),
            id="openai 503 naming a ramp",
        ),
    ],
)
def test_overloaded_server_error_is_always_overloaded(case: Case) -> None:
    assert _classify(case) == (ErrorClass.PROVIDER_5XX, RateLimitKind.OVERLOADED)


def test_overloaded_fault_is_still_unknown() -> None:
    case = Case(
        "openai",
        503,
        "service_unavailable_error",
        "server_is_overloaded",
        "The server is overloaded.",
        gateway_error_code="FAULT_PROVIDER_5XX",
    )

    assert _classify(case) == (ErrorClass.PROVIDER_5XX, RateLimitKind.UNKNOWN)
