"""Score a trace or span by asking a customer's endpoint, through a signed JSON POST.

The request body carries the redacted input, output and expected output as stored (each capped at
64 KB, the whole body at 256 KB; `truncated` is set when anything was cut). It is signed with the
alert channel's secret exactly as alert webhooks are. The endpoint answers 200 with
`{"scores": [...]}` (at most 20, at most 64 KB); anything else fails the run with a stable code:
`webhook_status_<code>`, `webhook_invalid_reply` or `webhook_unreachable`. One attempt, no retry,
no redirect, one 30-second deadline for the whole exchange. The URL and the secret never appear in
an error, a log line or the body.

`EvalInput.metadata` may carry `run_id`, `trace` and `span` (objects with the fields of the
payload); every other key is sent as `metadata`.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.alerts.channels.http import build_alert_client
from app.core.egress import BlockedAddress, InsecureUrl
from app.evals.config import WebhookEvaluatorConfig
from app.evals.evaluators.base import EvaluatorError
from app.evals.schemas import EvalInput, EvalResult
from app.notifications.signing import sign
from app.scores.schemas import Comment, ScoreIn, ScoreName, ScoreValue, StringValue
from app.scores.validation import ValidationFailure, validate_score

if TYPE_CHECKING:
    from app.alerts.channels.resolve import ResolvedChannel

PAYLOAD_VERSION = "2026-10-01"
TIMEOUT_SECONDS = 30.0
MAX_FIELD_BYTES = 64 * 1024
MAX_METADATA_BYTES = 16 * 1024
MAX_BODY_BYTES = 256 * 1024
MAX_REPLY_BYTES = 64 * 1024
MAX_SCORES = 20

logger = structlog.get_logger(__name__)


class _ReplyScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ScoreName
    value: ScoreValue | None = None
    string_value: StringValue | None = None
    comment: Comment | None = None


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scores: list[_ReplyScore] = Field(max_length=MAX_SCORES)


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def _cap(value: Any, limit: int) -> tuple[Any, bool]:
    """`value` itself if its JSON fits `limit` bytes, else the start of that JSON as a string."""
    text = _compact(value)
    raw = text.encode()
    if len(raw) <= limit:
        return value, False
    return raw[:limit].decode("utf-8", "ignore"), True


def build_body(input: EvalInput, *, evaluator_id: uuid.UUID | None, name: str) -> bytes:
    """The request body: the stored payload, capped. Raises `EvaluatorError` if still too big."""
    extra = dict(input.metadata)
    run_id = extra.pop("run_id", None)
    trace = extra.pop("trace", None)
    span = extra.pop("span", None)
    capped: dict[str, Any] = {}
    truncated = False
    for key, value, limit in (
        ("input", input.input, MAX_FIELD_BYTES),
        ("output", input.output, MAX_FIELD_BYTES),
        ("expected_output", input.expected_output, MAX_FIELD_BYTES),
        ("metadata", extra, MAX_METADATA_BYTES),
    ):
        capped[key], cut = _cap(value, limit)
        truncated = truncated or cut
    trace_fields = dict(trace) if isinstance(trace, dict) else {}
    trace_fields["trace_id"] = input.trace_id
    document = {
        "version": PAYLOAD_VERSION,
        "evaluator": {"id": str(evaluator_id) if evaluator_id else None, "name": name},
        "run_id": str(run_id) if run_id else None,
        "trace": trace_fields,
        "span": ({"span_id": input.span_id, **span} if isinstance(span, dict) else None)
        if input.span_id is not None
        else None,
        **capped,
        "truncated": truncated,
    }
    body = _compact(document).encode()
    if len(body) > MAX_BODY_BYTES:
        raise EvaluatorError("webhook_payload_too_large")
    return body


async def _read_reply(response: httpx.Response) -> bytes | None:
    """The raw reply body, or None when it is longer than `MAX_REPLY_BYTES`."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_raw():
        size += len(chunk)
        if size > MAX_REPLY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def parse_reply(raw: bytes | None, input: EvalInput) -> list[ScoreIn]:
    """The scores of a reply. Raises `EvaluatorError("webhook_invalid_reply")` on any defect."""
    if raw is None:
        raise EvaluatorError("webhook_invalid_reply")
    try:
        reply = _Reply.model_validate_json(raw)
        scores = [
            ScoreIn(
                trace_id=input.trace_id,
                span_id=input.span_id,
                name=item.name,
                value=item.value,
                string_value=item.string_value,
                comment=item.comment,
            )
            for item in reply.scores
        ]
    except ValidationError:
        raise EvaluatorError("webhook_invalid_reply") from None
    for score in scores:
        if isinstance(validate_score(score, None), ValidationFailure):
            raise EvaluatorError("webhook_invalid_reply")
    return scores


class WebhookEvaluator:
    """Posts each input to the channel's URL and turns the reply into scores."""

    def __init__(
        self,
        config: WebhookEvaluatorConfig,
        name: str,
        channel: ResolvedChannel,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        evaluator_id: uuid.UUID | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.name = name
        self._channel = channel
        self._evaluator_id = evaluator_id
        self._clock = clock
        self._client = client or build_alert_client(allow_private=False, transport=transport)

    async def evaluate(self, input: EvalInput) -> EvalResult:
        url = self._channel.config.get("url")
        secret = self._channel.secret
        if not isinstance(url, str) or not url or not secret:
            raise EvaluatorError("webhook_unreachable")
        body = build_body(input, evaluator_id=self._evaluator_id, name=self.name)
        timestamp = int(self._clock())
        headers = {
            "content-type": "application/json",
            "x-spanlight-timestamp": str(timestamp),
            "x-spanlight-signature": sign(secret.encode(), timestamp, body),
        }
        raw = await self._post(url, body, headers)
        return EvalResult(scores=parse_reply(raw, input))

    async def _post(self, url: str, body: bytes, headers: dict[str, str]) -> bytes | None:
        # httpx's timeout applies to each phase (connect, write, each read); the outer deadline
        # bounds the whole exchange, so a reply that drips in slowly still ends at 30 s.
        try:
            async with (
                asyncio.timeout(TIMEOUT_SECONDS),
                self._client.stream(
                    "POST", url, content=body, headers=headers, timeout=TIMEOUT_SECONDS
                ) as response,
            ):
                if response.status_code != 200:
                    raise EvaluatorError(f"webhook_status_{response.status_code}")
                return await _read_reply(response)
        except TimeoutError:
            raise EvaluatorError("webhook_unreachable") from None
        except (InsecureUrl, BlockedAddress, httpx.InvalidURL):
            raise EvaluatorError("webhook_unreachable") from None
        except httpx.TransportError:
            raise EvaluatorError("webhook_unreachable") from None
