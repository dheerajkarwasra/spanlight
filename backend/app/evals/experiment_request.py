"""The provider request of one experiment item, and the most it can cost. Pure: no database.

For a prompt-version target the item fills the variables: an `input` that is a JSON object whose
values are all strings is the variables as they are; any other input becomes `{"input": text}`,
its plain text. A variable the item cannot fill is the item's error
(`missing_variables: <names>`), never the run's. A messages target sends its messages unchanged.

Every request carries an output-token limit (the target's `max_tokens`, else
`DEFAULT_MAX_TOKENS`), so the estimate the cost cap reserves is a real ceiling.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.db.models import PromptVersion, ProviderKind
from app.evals.budget import estimate_call_cost
from app.evals.evaluators.heuristic import text_of
from app.evals.experiment_config import AdHocTarget, PromptVersionTarget
from app.gateway.context import GatewayRequest
from app.gateway.trace_headers import TraceHeaders
from app.playground import request as build
from app.playground.schemas import Params
from app.pricing.cost import PriceBook
from app.prompts.templates import Message, MissingVariablesError, TemplateTooLargeError

DEFAULT_MAX_TOKENS = 1024
TAG_PREFIX = "spanlight:experiment:"


@dataclass(frozen=True, slots=True)
class ItemRequest:
    """One item, ready to send, with its cost estimate; or why it cannot be sent."""

    request: GatewayRequest | None
    estimate: Decimal | None
    error: str | None


def output_ceiling(params: Params) -> int:
    return params.max_tokens or DEFAULT_MAX_TOKENS


def variables_for(item_input: Any) -> dict[str, str]:
    """The prompt variables an item's `input` provides."""
    if isinstance(item_input, dict) and all(isinstance(v, str) for v in item_input.values()):
        return {str(key): value for key, value in item_input.items()}
    return {"input": text_of(item_input)}


def messages_for(
    target: PromptVersionTarget | AdHocTarget, version: PromptVersion | None, item_input: Any
) -> list[Message]:
    """The messages to send for one item. Raises `MissingVariablesError` or
    `TemplateTooLargeError`."""
    if isinstance(target, AdHocTarget):
        return [Message(m.role, m.content) for m in target.messages]
    if version is None:
        raise ValueError("a prompt-version target needs its version")
    return build.compile_version(version, variables_for(item_input))


def item_request(
    target: PromptVersionTarget | AdHocTarget,
    version: PromptVersion | None,
    provider: ProviderKind,
    item_input: Any,
    *,
    run_tag: str,
    prices: PriceBook,
    at: datetime,
) -> ItemRequest:
    try:
        messages = messages_for(target, version, item_input)
    except MissingVariablesError as error:
        return ItemRequest(None, None, build.missing_variables_error(error))
    except TemplateTooLargeError:
        return ItemRequest(None, None, build.PROMPT_TOO_LARGE)
    max_tokens = output_ceiling(target.params)
    params = target.params.model_copy(update={"max_tokens": max_tokens})
    body = build.build_body(provider, target.model, params, messages)
    estimate = estimate_call_cost(
        target.model,
        "".join(m.content for m in messages),
        max_tokens,
        prices,
        provider=provider.value,
        at=at,
    )
    request = GatewayRequest(
        surface=build.surface_for(provider),
        body=body,
        stream=False,
        trace=TraceHeaders(tags=[run_tag]),
    )
    return ItemRequest(request, estimate, None)


def run_tag(run_id: object) -> str:
    return f"{TAG_PREFIX}{run_id}"


def priced(
    target: PromptVersionTarget | AdHocTarget,
    provider: ProviderKind,
    prices: PriceBook,
    at: datetime,
) -> bool:
    """Whether the target's model has a positive price, so the cost cap can be enforced."""
    estimate = estimate_call_cost(
        target.model, 1, output_ceiling(target.params), prices, provider=provider.value, at=at
    )
    return estimate is not None and estimate > 0
