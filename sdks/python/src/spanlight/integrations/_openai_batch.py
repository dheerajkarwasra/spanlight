"""OpenAI Batch API output files recorded as spans.

The Batch API writes one JSONL line per request to the batch's output file (and failed
requests to its error file)::

    {"id": "batch_req_…", "custom_id": "request-1",
     "response": {"status_code": 200, "request_id": "req_…", "body": {…}},
     "error": null}

:func:`record_openai_batch_output` hands every line to
:func:`~spanlight.integrations._batch.record_batch_item`. The body is a chat completion or
a Responses object, read the same way the live wrapper reads them. Parsing uses only the
standard library, so the ``openai`` package is not imported.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from ._batch import SUCCEEDED, record_batch_item
from ._common import client_getter, get_field
from ._openai_response import (
    chat_output,
    first_str,
    responses_output,
    usage_fields,
    web_search_calls,
)

if TYPE_CHECKING:
    from .._client import Spanlight

logger = logging.getLogger("spanlight")

ERRORED = "errored"


def record_openai_batch_output(
    lines: Iterable[str | bytes],
    *,
    batch_id: str,
    provider: str = "openai",
    spanlight: Spanlight | None = None,
) -> int:
    """Record the lines of an OpenAI Batch output or error file as ``llm`` spans.

    Each request becomes one span named ``batch <model>`` in a trace named
    ``batch <batch_id>``, priced at the batch rate (``service_tier`` is ``batch``; the
    tier the body reported is kept in ``attributes["usage.service_tier_raw"]``). A line
    whose ``status_code`` is not 200, or that carries an ``error``, becomes an error span
    without usage; a Responses body with ``status: "failed"`` becomes an error span that
    keeps the usage it reports, and ``status: "incomplete"`` a normal span whose finish
    reason is ``incomplete_details.reason``. Ids derive from ``batch_id`` and each
    ``custom_id``, so recording the same file twice updates the same spans. Never raises;
    unreadable lines are skipped and logged at debug level.

    Args:
        lines: The file's lines, e.g. ``client.files.content(file_id).iter_lines()``.
            A whole file passed as one ``str`` or ``bytes`` is split into lines.
        batch_id: The batch's id (``batch_…``).
        provider: Provider name recorded on spans; override it for a compatible API.
        spanlight: Spanlight client to report to; defaults to the global client.

    Returns:
        The number of spans recorded.
    """
    recorded = 0
    try:
        client = client_getter(spanlight)()
        if isinstance(lines, (str, bytes)):
            lines = lines.splitlines()
        for line in lines:
            if _record_line_safely(client, line, batch_id=batch_id, provider=provider):
                recorded += 1
    except Exception:
        logger.debug("Stopped reading the output of batch %r", batch_id, exc_info=True)
    return recorded


def _record_line_safely(client: Spanlight, line: Any, *, batch_id: str, provider: str) -> bool:
    """Record one line; a line of an unexpected shape is skipped, never ends the file."""
    try:
        return _record_line(client, line, batch_id=batch_id, provider=provider)
    except Exception:
        logger.debug(
            "Skipping an unreadable line in the output of batch %r", batch_id, exc_info=True
        )
        return False


def _record_line(client: Spanlight, line: str | bytes, *, batch_id: str, provider: str) -> bool:
    if not line.strip():
        return False
    parsed = _parse(line)
    if parsed is None:
        logger.debug("Skipping an unreadable line in the output of batch %r", batch_id)
        return False
    custom_id, item, response = parsed
    error, error_message = _error(item, response)
    # A body is read only from a 200 response: a failed Responses object still reports
    # the usage it is billed for, while a non-200 body is an error envelope.
    body = get_field(response, "body") if get_field(response, "status_code") == 200 else None
    output, finish_reasons = (None, []) if body is None else _output(body)
    model = get_field(body, "model")
    return record_batch_item(
        client,
        provider=provider,
        batch_id=batch_id,
        custom_id=custom_id,
        result_type=SUCCEEDED if error is None else ERRORED,
        model=model if isinstance(model, str) else None,
        usage=None if body is None else _usage(body),
        output=output,
        finish_reason=first_str(finish_reasons),
        error=error,
        error_message=error_message,
    )


def _parse(line: str | bytes) -> tuple[str, Mapping[str, Any], Any] | None:
    """The ``custom_id``, the line and its ``response``, or ``None`` for an unreadable line.

    A readable line has a string ``custom_id`` and either an ``error`` or a ``response``
    object; a successful response must carry its ``body`` object.
    """
    try:
        item = json.loads(line)
    except ValueError:
        return None
    if not isinstance(item, Mapping) or not isinstance(item.get("custom_id"), str):
        return None
    response = item.get("response")
    if item.get("error") is None:
        if not isinstance(response, Mapping):
            return None
        if response.get("status_code") == 200 and not isinstance(response.get("body"), Mapping):
            return None
    return item["custom_id"], item, response


def _error(item: Any, response: Any) -> tuple[str | None, str | None]:
    """The error class and message of a failed request, or ``(None, None)``."""
    line_error = get_field(item, "error")
    if line_error is not None:
        return _error_fields(line_error, fallback=ERRORED)
    status_code = get_field(response, "status_code")
    if status_code != 200:
        body_error = get_field(response, "body", "error")
        return _error_fields(body_error, fallback=f"http_{status_code}")
    if get_field(response, "body", "status") == "failed":
        return _error_fields(get_field(response, "body", "error"), fallback="failed")
    return None, None


def _error_fields(error: Any, *, fallback: str) -> tuple[str, str | None]:
    error_class = get_field(error, "type") or get_field(error, "code")
    message = get_field(error, "message")
    return (
        error_class if isinstance(error_class, str) and error_class else fallback,
        message if isinstance(message, str) else None,
    )


def _usage(body: Any) -> dict[str, Any]:
    usage = get_field(body, "usage")
    fields: dict[str, Any] = {} if usage is None else dict(usage_fields(usage))
    fields["web_search_requests"] = web_search_calls(body)
    tier = get_field(body, "service_tier")
    fields["service_tier"] = tier if isinstance(tier, str) else None
    return fields


def _output(body: Any) -> tuple[Any, list[Any]]:
    """The response content and finish reasons, for a chat or a Responses body."""
    if get_field(body, "choices") is not None:
        return chat_output(body)
    reason = get_field(body, "incomplete_details", "reason")
    return responses_output(body), [reason] if get_field(body, "status") == "incomplete" else []
