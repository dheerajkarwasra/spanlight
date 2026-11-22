"""Conservative reservation for bounded Gemini Developer API text requests."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, NamedTuple

from app.gateway.sse import as_int
from app.pricing.cost import MAX_PRICED_TOKENS, PriceBook

# Published model limits. Allow additional output headroom for thinking rather than assuming
# maxOutputTokens bounds both generated content and thoughts on every model/SDK combination.
_MODELS = {
    "gemini-2.5-flash-lite": (1_048_576, 65_536, 24_576),
    "gemini-3.5-flash-lite": (1_048_576, 65_536, 65_536),
    "gemini-3.8-flash": (1_048_576, 65_536, 65_536),
}
_BODY_FIELDS = frozenset(
    {
        "model",
        "stream",
        "contents",
        "systemInstruction",
        "generationConfig",
        "tools",
        "toolConfig",
        "safetySettings",
    }
)
_GENERATION_FIELDS = frozenset(
    {
        "maxOutputTokens",
        "candidateCount",
        "responseModalities",
        "thinkingConfig",
        "temperature",
        "topP",
        "topK",
        "seed",
        "stopSequences",
        "responseMimeType",
        "responseSchema",
        "responseJsonSchema",
        "presencePenalty",
        "frequencyPenalty",
    }
)
_THINKING_FIELDS = frozenset({"thinkingBudget", "thinkingLevel", "includeThoughts"})
_TEXT_MIME = ("text/plain", "application/json", "text/x.enum")
UNBOUNDED_GEMINI = "Gemini spend cannot be bounded for this model, input, or configuration"


class GeminiReservation(NamedTuple):
    usd: Decimal
    clamp: int | None


def _text_content(content: object) -> bool:
    if not isinstance(content, dict) or content.keys() - {"role", "parts"}:
        return False
    parts = content.get("parts")
    return (
        isinstance(parts, list)
        and bool(parts)
        and all(
            isinstance(part, dict) and part.keys() == {"text"} and isinstance(part["text"], str)
            for part in parts
        )
    )


def _bounded_body(body: Mapping[str, Any]) -> bool:
    if body.keys() - _BODY_FIELDS:
        return False
    contents = body.get("contents")
    if not isinstance(contents, list) or not contents or not all(map(_text_content, contents)):
        return False
    if "systemInstruction" in body and not _text_content(body["systemInstruction"]):
        return False
    tools = body.get("tools", [])
    return isinstance(tools, list) and all(
        isinstance(tool, dict)
        and tool.keys() == {"functionDeclarations"}
        and isinstance(tool["functionDeclarations"], list)
        for tool in tools
    )


def _bounded_config(config: object) -> bool:
    if not isinstance(config, dict) or config.keys() - _GENERATION_FIELDS:
        return False
    thinking = config.get("thinkingConfig", {})
    return (
        config.get("responseModalities", ["TEXT"]) == ["TEXT"]
        and config.get("responseMimeType", "text/plain") in _TEXT_MIME
        and isinstance(thinking, dict)
        and not (thinking.keys() - _THINKING_FIELDS)
    )


def supports_text_billing(body: Mapping[str, Any]) -> bool:
    """Whether the request's billing dimensions are covered by standard text rates."""
    return _bounded_body(body) and _bounded_config(body.get("generationConfig", {}))


def reserve_gemini(
    body: Mapping[str, Any],
    models: Sequence[str],
    prices: PriceBook,
    *,
    at: datetime,
    fallback_cap: int,
) -> GeminiReservation | None:
    """Bound every eligible target before dispatch, or report that the spend is unknown."""
    config = body.get("generationConfig", {})
    if not supports_text_billing(body) or not models:
        return None
    cap = as_int(config.get("maxOutputTokens", fallback_cap))
    candidates = as_int(config.get("candidateCount", 1))
    if cap is None or cap <= 0 or candidates is None or candidates <= 0:
        return None
    costs: list[Decimal] = []
    for model in set(models):
        limits = _MODELS.get(model.removeprefix("models/"))
        if limits is None or cap > limits[1]:
            return None
        prompt, _, thinking = limits
        output = (cap + thinking) * candidates
        if output > MAX_PRICED_TOKENS:
            return None
        bound = prices.token_cost_ceiling(
            "google", model.removeprefix("models/"), at, prompt, output
        )
        if bound is None:
            return None
        costs.append(bound)
    return GeminiReservation(max(costs), None if "maxOutputTokens" in config else fallback_cap)
