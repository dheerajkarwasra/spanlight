"""Anthropic clients (direct and through the gateway) and the ledger-guarded call helpers.

The key comes from `ANTHROPIC_API_KEY` at call time and is sent only to Anthropic (directly) or
to the local gateway. Every paid call the harness makes goes through `guarded_create` or
`guarded_stream`, which wrap `Ledger.guard(...)`: the call is refused before it is made when it
could pass the soft cap.
"""

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import anthropic

from accuracy.doctor.common import FAULT_HEADER, SESSION_HEADER, TAGS_HEADER, spend_ledger
from accuracy.ledger import Usage
from accuracy.paths import anthropic_api_key
from accuracy.setup import HOST


def direct_client(timeout: float = 120.0) -> Any:
    """An Anthropic client that talks to Anthropic directly, with the SDK's retries off."""
    return anthropic.Anthropic(api_key=anthropic_api_key(), max_retries=0, timeout=timeout)


def gateway_client(
    gateway_key: str,
    *,
    session_id: str | None = None,
    tags: Sequence[str] = (),
    timeout: float = 120.0,
    host: str = HOST,
) -> Any:
    """An Anthropic client pointed at the local gateway (`/gw/v1/messages`), retries off."""
    headers: dict[str, str] = {}
    if session_id:
        headers[SESSION_HEADER] = session_id
    if tags:
        headers[TAGS_HEADER] = ",".join(tags)
    return anthropic.Anthropic(
        api_key=gateway_key,
        base_url=f"{host}/gw",
        max_retries=0,
        timeout=timeout,
        default_headers=headers or None,
    )


def est_tokens(*texts: str) -> int:
    """A conservative prompt-size estimate for the ledger reservation (3 chars per token)."""
    return sum(len(t) // 3 for t in texts) + 32


def _fault_header(exc: BaseException) -> str | None:
    """The Integration Lab's fault header on a failed call's response, if any."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get(FAULT_HEADER)
    return value if isinstance(value, str) else None


def guarded_create(
    client: Any,
    *,
    scenario: str,
    model: str,
    path: str,
    est_prompt_tokens: int,
    max_tokens: int,
    **kwargs: Any,
) -> Any:
    """`client.messages.create(...)` under the ledger. Re-raises provider errors unchanged.

    A call the Integration Lab answered itself (the `X-Spanlight-Fault` header) never reached
    the provider and settles at zero; any other failure settles at the worst case.
    """
    with spend_ledger().guard(
        scenario=scenario,
        model=model,
        est_prompt_tokens=est_prompt_tokens,
        max_tokens=max_tokens,
        path=path,
    ) as slot:
        response = _create(client, slot, model=model, max_tokens=max_tokens, **kwargs)
        slot["usage"] = Usage.from_anthropic(response.usage)
        return response


def _create(client: Any, slot: dict[str, Any], **kwargs: Any) -> Any:
    """`client.messages.create(**kwargs)`; a failure the Integration Lab answered itself (the
    `X-Spanlight-Fault` header) never reached the provider, so its slot settles at zero."""
    try:
        return client.messages.create(**kwargs)
    except Exception as exc:
        fault = _fault_header(exc)
        if fault:
            slot["usage"] = Usage()
            slot["extra"] = {"fault": fault}
        raise


@dataclass
class StreamOutcome:
    """What one streamed call produced: text, the stop reason (None when the stream ended
    without its final events) and the exception that cut it, if any."""

    text: str = ""
    stop_reason: str | None = None
    events: int = 0
    broke: BaseException | None = None
    model: str | None = None


def _read_stream(stream: Any, outcome: StreamOutcome, usage: dict[str, Any]) -> bool:
    """Read `stream` to its end into `outcome` and `usage`; True when `message_stop` arrived."""
    complete = False
    for event in stream:
        outcome.events += 1
        kind = getattr(event, "type", None)
        if kind == "message_start":
            message = event.message
            outcome.model = getattr(message, "model", None)
            _merge_usage(usage, getattr(message, "usage", None))
        elif kind == "content_block_delta":
            delta = event.delta
            if getattr(delta, "type", None) == "text_delta":
                outcome.text += delta.text
        elif kind == "message_delta":
            outcome.stop_reason = getattr(event.delta, "stop_reason", None)
            _merge_usage(usage, getattr(event, "usage", None))
        elif kind == "message_stop":
            complete = True
    return complete


def guarded_stream(
    client: Any,
    *,
    scenario: str,
    model: str,
    path: str,
    est_prompt_tokens: int,
    max_tokens: int,
    **kwargs: Any,
) -> StreamOutcome:
    """A streamed `messages.create(stream=True)` read to its end, under the ledger.

    A stream that ends without `message_stop` (the Lab's `truncated_stream`, a broken
    connection) settles at the worst case: the provider may have billed output the client never
    saw. HTTP errors are raised unchanged.
    """
    with spend_ledger().guard(
        scenario=scenario,
        model=model,
        est_prompt_tokens=est_prompt_tokens,
        max_tokens=max_tokens,
        path=path,
    ) as slot:
        stream = _create(client, slot, model=model, max_tokens=max_tokens, stream=True, **kwargs)
        outcome = StreamOutcome()
        usage: dict[str, Any] = {}
        complete = _drain(stream, outcome, usage)
        if complete and usage:
            slot["usage"] = Usage.from_anthropic(usage)
        slot["extra"] = {"complete": complete, "events": outcome.events}
        return outcome


def _drain(stream: Any, outcome: StreamOutcome, usage: dict[str, Any]) -> bool:
    """Read `stream` to its end and close it; True when `message_stop` arrived. A connection
    that breaks mid-stream is recorded on `outcome`: the client's problem to judge."""
    try:
        return _read_stream(stream, outcome, usage)
    except Exception as exc:  # noqa: BLE001 - recorded on the outcome, never swallowed
        outcome.broke = exc
        return False
    finally:
        # Closing a connection that already broke can raise again; the outcome has it.
        with contextlib.suppress(Exception):
            stream.close()


def _merge_usage(target: dict[str, Any], usage: Any) -> None:
    if usage is None:
        return
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            target[key] = value
