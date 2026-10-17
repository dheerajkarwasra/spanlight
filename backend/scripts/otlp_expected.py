"""The rows Spanlight stores for one OTLP export, as a fixture's `expected` section (spec §8.1).

Run from `backend/` on the bytes an exporter sent:

    uv run python scripts/otlp_expected.py capture.bin --content-type application/x-protobuf

The export is decoded, mapped and normalized exactly as `POST /v1/otlp/traces` does it, priced
from the seed snapshot without a database, and printed as JSON. The usage-rollup flag of each
span is derived over the batch with the rule of spec §5.1 (a span with usage whose descendant
has usage), and the trace totals exclude rollups the way the stored trace does. The fixture
recorder (`sdks/fixtures/otlp/record`) calls this to write `expected`; the contract test then
asserts the stored rows against it.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.db.models.telemetry import SpanKind
from app.ingest import otlp
from app.ingest.normalize import NormalizedSpan, SpanRejectedError, normalize_span
from app.ingest.pipeline import _first_error, parse_json_body
from app.ingest.rate_limit_kind import RateLimitKind
from app.ingest.schemas import SpanIn
from app.ingest.usage_state import usage_state
from app.pricing.cost import PriceBook
from app.pricing.prices import seed_price_book

_PROTOBUF = "application/x-protobuf"
_MAX_DEPTH = 64  # as the stored recompute: bounds a malformed parent cycle
_DUPLICATE = "duplicate span_id in batch; the later occurrence was kept"
_DESCRIPTION = "Print the expected fixture rows for one OTLP export as JSON."


def expected_for(
    body: bytes, content_type: str, *, prices: PriceBook, now: datetime
) -> dict[str, Any]:
    """The `expected` section for one export: accepted count, rejections, spans, trace totals."""
    decoded = (
        otlp.decode_protobuf(body)
        if content_type == _PROTOBUF
        else otlp.decode_json(parse_json_body(body))
    )
    native, _ = otlp.to_native_spans(decoded)
    rejected: list[dict[str, Any]] = []
    accepted: dict[tuple[str, str], NormalizedSpan] = {}
    for raw in native:
        try:
            span = normalize_span(
                SpanIn.model_validate(raw), capture_payloads=True, prices=prices, now=now
            )
        except ValidationError as exc:
            rejected.append({"span_id": raw.get("span_id"), "reason": _first_error(exc)})
            continue
        except SpanRejectedError as exc:
            rejected.append({"span_id": raw.get("span_id"), "reason": str(exc)})
            continue
        if (span.trace_id, span.span_id) in accepted:
            rejected.append({"span_id": span.span_id, "reason": _DUPLICATE})
        accepted[(span.trace_id, span.span_id)] = span
    spans = list(accepted.values())
    rollups = usage_rollups(spans)
    return {
        "accepted": len(spans),
        "rejected": rejected,
        "spans": [_span_row(span, span.span_id in rollups) for span in spans],
        "trace": _trace_totals(spans, rollups),
        "loses": [],
    }


def usage_rollups(spans: list[NormalizedSpan]) -> set[str]:
    """The span ids flagged `usage_rollup` by spec §5.1, derived over this batch alone."""
    by_key = {(span.trace_id, span.span_id): span for span in spans}
    rollups: set[str] = set()
    for span in spans:
        if not _has_usage(span):
            continue
        ancestor, depth = span.parent_span_id, 0
        while ancestor is not None and depth < _MAX_DEPTH:
            parent = by_key.get((span.trace_id, ancestor))
            if parent is None:
                break
            if _has_usage(parent):
                rollups.add(parent.span_id)
            ancestor, depth = parent.parent_span_id, depth + 1
    return rollups


def _has_usage(span: NormalizedSpan) -> bool:
    return span.input_tokens is not None or span.output_tokens is not None


def _span_row(span: NormalizedSpan, rollup: bool) -> dict[str, Any]:
    state = usage_state(
        kind=span.kind,
        model=span.model,
        input_tokens=span.input_tokens,
        output_tokens=span.output_tokens,
        cost_usd=span.cost_usd,
        usage_rollup=rollup,
    )
    return {
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "convention": span.attributes["spanlight.convention"],
        "kind": span.kind.value,
        "provider": span.provider,
        "model": span.model,
        "usage": {
            "input_tokens": span.input_tokens,
            "output_tokens": span.output_tokens,
            "cached_tokens": span.cached_tokens,
            "cache_write_tokens": span.cache_write_tokens,
        },
        "usage_rollup": rollup,
        "usage_state": state.value,
        "finish_reason": span.finish_reason,
        "input_present": span.input is not None,
        "output_present": span.output is not None,
        "time_to_first_token_ms": span.time_to_first_token_ms,
        "status": span.status.value,
        "error_class": span.error_class.value if span.error_class else None,
        "operation": span.attributes.get("spanlight.operation"),
        "trace": {
            "name": span.trace.name,
            "environment": span.trace.environment,
            "release": span.trace.release,
            "user_id": span.trace.external_user_id,
            "session_id": span.trace.session_id,
            "metadata": span.trace.metadata,
        },
    }


def _trace_totals(spans: list[NormalizedSpan], rollups: set[str]) -> dict[str, Any]:
    """The stored trace totals (`store._RECOMPUTE_ROLLUPS`): rollups are counted, never summed."""
    billable = [span for span in spans if span.span_id not in rollups]
    return {
        "span_count": len(spans),
        "input_tokens": sum(span.input_tokens or 0 for span in billable),
        "output_tokens": sum(span.output_tokens or 0 for span in billable),
        "has_unpriced": any(
            (span.kind == SpanKind.LLM or span.model is not None)
            and span.cost_usd is None
            and span.rate_limit_kind != RateLimitKind.GATEWAY
            for span in billable
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("capture", type=Path, help="the exported bytes, as the exporter sent them")
    parser.add_argument(
        "--content-type", default=_PROTOBUF, choices=[_PROTOBUF, "application/json"]
    )
    args = parser.parse_args(argv)
    expected = expected_for(
        args.capture.read_bytes(),
        args.content_type,
        prices=seed_price_book(),
        now=datetime.now(UTC),
    )
    json.dump(expected, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
