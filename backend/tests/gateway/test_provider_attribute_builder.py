"""The provider attributes of a gateway span: status, error codes, request id, limits, attempts.

Pure: `provider_attributes.build` reads a `GatewayResult` and the context's route and
credentials. The context's services are placeholders because the builder never touches them.
"""

import json
import uuid
from typing import Any, cast

from app.db.models import ProviderCredential
from app.db.models.gateway import ProviderKind
from app.gateway import provider_attributes
from app.gateway.context import UPSTREAM_ERROR_CODE, Attempt, GatewayContext, GatewayResult
from app.gateway.errors import ErrorKind, GatewayError, rate_limited
from app.gateway.route_config import FallbackPolicy, RetryPolicy, RouteConfig, Target
from app.gateway.upstream_io import upstream_failure

OPENAI_SECRET = "sk-proj-known-openai-secret-0001"
ANTHROPIC_SECRET = "sk-ant-api03-known-anthropic-secret-0002"

SUCCESS_HEADERS = {
    "content-type": "application/json",
    "x-upstream-request-id": "req_abc123",
    "retry-after": "3",
    "x-ratelimit-limit-requests": "500",
    "x-ratelimit-remaining-requests": "499",
    "x-ratelimit-reset-requests": "120ms",
    "anthropic-ratelimit-tokens-limit": "80000",
    "anthropic-ratelimit-tokens-remaining": "79000",
    "anthropic-ratelimit-tokens-reset": "2026-10-16T10:00:00Z",
}
# Every allowlisted header of `SUCCESS_HEADERS`: what a failed call keeps.
RATELIMIT_HEADERS = {
    name: value
    for name, value in SUCCESS_HEADERS.items()
    if name not in ("content-type", "x-upstream-request-id")
}


def _credential(name: str, provider: ProviderKind, secret: str) -> ProviderCredential:
    """A credential whose stored bytes are its clear key, so a leak of them would be visible."""
    return ProviderCredential(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        provider=provider,
        name=name,
        ciphertext=secret.encode(),
        key_id="test",
    )


OPENAI = _credential("openai-prod", ProviderKind.OPENAI, OPENAI_SECRET)
ANTHROPIC = _credential("anthropic-backup", ProviderKind.ANTHROPIC, ANTHROPIC_SECRET)


def _context() -> GatewayContext:
    route = RouteConfig(
        targets=[Target(credential_id=OPENAI.id), Target(credential_id=ANTHROPIC.id)],
        retry=RetryPolicy(),
        fallback=FallbackPolicy(),
    )
    unused = cast(Any, None)
    return GatewayContext(
        key=None,
        project_id=uuid.uuid4(),
        org_id=OPENAI.org_id,
        environment="test",
        route=route,
        route_id=uuid.uuid4(),
        route_name="default",
        route_version=1,
        credentials={OPENAI.id: OPENAI, ANTHROPIC.id: ANTHROPIC},
        capture_payloads=False,
        budget_guard=unused,
        cache=unused,
        http=unused,
        sessions=unused,
        settings=unused,
    )


def _result(
    status: int,
    headers: dict[str, str],
    *,
    error: GatewayError | None = None,
    attempts: list[Attempt] | None = None,
) -> GatewayResult:
    return GatewayResult(
        status=status,
        headers=headers,
        body=b"{}",
        stream=None,
        trace_id="0" * 32,
        span_id="0" * 16,
        attempts=attempts if attempts is not None else [],
        error=error,
    )


def _upstream_error(openai_kind: ErrorKind, status: int = 429) -> GatewayError:
    return GatewayError(
        status, UPSTREAM_ERROR_CODE, "refused", openai=openai_kind, anthropic=openai_kind
    )


def test_status_is_always_set() -> None:
    success = _result(200, {})
    gateway_made = _result(429, {}, error=rate_limited("rpm", 60, 5.0))
    passed_through = _result(503, {}, error=upstream_failure(503, b"not json"))

    for result in (success, gateway_made, passed_through):
        attributes = provider_attributes.build(result, _context())
        assert attributes["spanlight.gateway.status"] == result.status


def test_gateway_error_code_is_absent_for_passed_through_provider_errors() -> None:
    passed_through = _result(429, {}, error=_upstream_error(ErrorKind("tokens")))
    gateway_made = _result(429, {}, error=rate_limited("rpm", 60, 5.0))
    success = _result(200, {})

    assert "spanlight.gateway.error_code" not in provider_attributes.build(
        passed_through, _context()
    )
    assert "spanlight.gateway.error_code" not in provider_attributes.build(success, _context())
    made = provider_attributes.build(gateway_made, _context())
    assert made["spanlight.gateway.error_code"] == "RATE_LIMITED"
    assert "provider.error.type" not in made
    assert "provider.error.code" not in made


def test_gateway_made_error_records_no_provider_headers() -> None:
    error = rate_limited("rpm", 60, 5.0)
    headers = {"Retry-After": "5", "x-upstream-request-id": "req_x", "X-Spanlight-Code": "x"}

    attributes = provider_attributes.build(_result(429, headers, error=error), _context())

    assert "provider.ratelimit" not in attributes
    assert "provider.request_id" not in attributes


def test_provider_error_type_and_code_from_openai_and_anthropic_kinds() -> None:
    openai = _upstream_error(ErrorKind("tokens", "rate_limit_exceeded"))
    anthropic = _upstream_error(ErrorKind("rate_limit_error", "enforced_spend_limit_reached"))
    overloaded_body = b'{"type":"error","error":{"type":"overloaded_error","message":"Busy"}}'
    no_code = upstream_failure(529, overloaded_body)

    from_openai = provider_attributes.build(_result(429, {}, error=openai), _context())
    from_anthropic = provider_attributes.build(_result(429, {}, error=anthropic), _context())
    without_code = provider_attributes.build(_result(529, {}, error=no_code), _context())

    assert from_openai["provider.error.type"] == "tokens"
    assert from_openai["provider.error.code"] == "rate_limit_exceeded"
    assert from_anthropic["provider.error.type"] == "rate_limit_error"
    assert from_anthropic["provider.error.code"] == "enforced_spend_limit_reached"
    assert without_code["provider.error.type"] == "overloaded_error"
    assert "provider.error.code" not in without_code


def test_snapshot_is_reduced_on_success_and_whole_on_failure() -> None:
    success = provider_attributes.build(_result(200, SUCCESS_HEADERS), _context())
    failed = provider_attributes.build(
        _result(429, SUCCESS_HEADERS, error=_upstream_error(ErrorKind("tokens"))), _context()
    )
    bare = provider_attributes.build(_result(200, {"content-type": "text/plain"}), _context())

    assert success["provider.ratelimit"] == {
        "x-ratelimit-limit-requests": "500",
        "x-ratelimit-remaining-requests": "499",
        "anthropic-ratelimit-tokens-limit": "80000",
        "anthropic-ratelimit-tokens-remaining": "79000",
    }
    assert failed["provider.ratelimit"] == RATELIMIT_HEADERS
    assert "provider.ratelimit" not in bare


def test_request_id_from_upstream_header() -> None:
    with_id = provider_attributes.build(_result(200, SUCCESS_HEADERS), _context())
    without_id = provider_attributes.build(_result(200, {}), _context())

    assert with_id["provider.request_id"] == "req_abc123"
    assert "provider.request_id" not in without_id


def test_attempt_log_names_targets_and_is_absent_without_attempts() -> None:
    attempts = [
        Attempt(0, OPENAI.id, 429, None, 812.4, retry_after=2.0),
        Attempt(1, ANTHROPIC.id, 200, None, 301.0),
    ]

    attributes = provider_attributes.build(_result(200, {}, attempts=attempts), _context())
    no_attempts = provider_attributes.build(_result(200, {}), _context())

    assert attributes["spanlight.gateway.attempt_log"] == [
        {
            "target": 0,
            "name": "openai-prod",
            "status": 429,
            "error": None,
            "duration_ms": 812.4,
            "retry_after_s": 2.0,
        },
        {
            "target": 1,
            "name": "anthropic-backup",
            "status": 200,
            "error": None,
            "duration_ms": 301.0,
            "retry_after_s": None,
        },
    ]
    assert "spanlight.gateway.attempt_log" not in no_attempts


def test_entries_hold_no_secret_substrings() -> None:
    attempts = [
        Attempt(i % 2, (OPENAI if i % 2 == 0 else ANTHROPIC).id, 500, None, 10.0 + i)
        for i in range(14)
    ]
    headers = {
        **SUCCESS_HEADERS,
        "authorization": f"Bearer {OPENAI_SECRET}",
        "x-api-key": ANTHROPIC_SECRET,
    }
    result = _result(
        500, headers, error=_upstream_error(ErrorKind("server_error")), attempts=attempts
    )

    attributes = provider_attributes.build(result, _context())
    dumped = json.dumps(attributes["spanlight.gateway.attempt_log"]).lower()
    whole = json.dumps(attributes).lower()

    for forbidden in ("authorization", "x-api-key", "bearer", "sk-"):
        assert forbidden not in dumped
        assert forbidden not in whole
    assert OPENAI_SECRET.lower() not in whole
    assert ANTHROPIC_SECRET.lower() not in whole


def test_cache_hit_records_no_provider_headers() -> None:
    # A cache hit answers from storage: no provider spoke on this call, whatever the headers say.
    result = _result(200, SUCCESS_HEADERS)
    result.cache = "hit"

    attributes = provider_attributes.build(result, _context())

    assert attributes["spanlight.gateway.status"] == 200
    for absent in (
        "provider.request_id",
        "provider.ratelimit",
        "spanlight.gateway.attempt_log",
        "provider.error.type",
        "provider.error.code",
        "spanlight.gateway.error_code",
    ):
        assert absent not in attributes
