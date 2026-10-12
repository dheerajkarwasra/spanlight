"""What a playground run shows of the gateway's answer. Pure."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.gateway.context import GatewayResult
from app.gateway.errors import Surface
from app.gateway.response_summary import summarize_response
from app.gateway.sse import Usage
from app.gateway.tracing import status_message
from app.playground.schemas import PlaygroundResult
from app.pricing.cost import PriceBook

BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
MAX_ERROR_CHARS = 500


def output_text(output: dict[str, Any] | None) -> str | None:
    """The assistant's text from a span-shaped `output` (a chat message or Anthropic content)."""
    if output is None:
        return None
    content = output.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "".join(parts).strip() or None


def error_text(result: GatewayResult) -> str | None:
    """Why the run failed, or None when it answered. A budget block is `BUDGET_EXCEEDED`."""
    if result.budget is not None:
        return BUDGET_EXCEEDED
    message = status_message(result)
    if message is None and result.status >= 400:
        message = f"{result.status} upstream error"
    return message[:MAX_ERROR_CHARS] if message is not None else None


def result_of(
    result: GatewayResult,
    *,
    surface: Surface,
    provider: str,
    model: str,
    prices: PriceBook,
    latency_ms: float,
    at: datetime,
) -> PlaygroundResult:
    """The row of one finished call (success or failure)."""
    error = error_text(result)
    output = None
    if error is None:
        shown = result.output
        if shown is None and result.body is not None:
            shown = summarize_response(surface, result.body).output
        output = output_text(shown)
        if output is None:
            error = "the provider's answer had no text"
    return PlaygroundResult(
        output=output,
        usage=result.usage,
        cost_usd=_cost(prices, provider, result.model or model, result.usage, at),
        latency_ms=latency_ms,
        ttft_ms=None,
        trace_id=result.trace_id,
        span_id=result.span_id,
        error=error,
    )


def _cost(
    prices: PriceBook, provider: str, model: str, usage: Usage | None, at: datetime
) -> Decimal | None:
    if usage is None:
        return None
    priced = prices.cost(
        provider=provider,
        model=model,
        at=at,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
    )
    return priced.cost_usd if priced is not None else None


def failed(error: str) -> PlaygroundResult:
    """A run that never reached the gateway, or whose call broke on our side."""
    return PlaygroundResult(None, None, None, None, None, None, None, error)
