"""``tracer.span`` context managers, ``update_trace`` and context propagation."""

from __future__ import annotations

import asyncio
import contextvars
import threading

import pytest
from support import flushed_spans

import spanlight

# ---------------------------------------------------------------------- #
# Span context manager
# ---------------------------------------------------------------------- #


def test_span_setters_are_exported(tracer, ingest):
    with tracer.span("summarize", kind="llm", input={"text": "long"}) as span:
        span.set_model("claude-haiku-4-5", provider="anthropic")
        span.set_usage(input_tokens=120, output_tokens=30, cached_tokens=100)
        span.set_attribute("temperature", 0.2)
        span.set_attributes({"attempt": 1})
        span.mark_first_token()
        span.set_output("short")

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["name"] == "summarize"
    assert exported["kind"] == "llm"
    assert exported["status"] == "ok"
    assert exported["provider"] == "anthropic"
    assert exported["model"] == "claude-haiku-4-5"
    assert exported["usage"] == {"input_tokens": 120, "output_tokens": 30, "cached_tokens": 100}
    assert exported["time_to_first_token_ms"] >= 0
    assert exported["input"] == {"text": "long"}
    assert exported["output"] == "short"
    assert exported["attributes"] == {"temperature": 0.2, "attempt": 1}


def test_set_usage_omits_unset_write_fields(tracer, ingest):
    with tracer.span("reads-only", kind="llm") as span:
        span.set_usage(input_tokens=120, output_tokens=30, cached_tokens=100)
    with tracer.span("writes-5m", kind="llm") as span:
        span.set_usage(input_tokens=120, output_tokens=30, cache_write_tokens=20)
    with tracer.span("writes-split", kind="llm") as span:
        span.set_usage(
            input_tokens=120,
            output_tokens=30,
            cached_tokens=50,
            cache_write_tokens=20,
            cache_write_1h_tokens=5,
        )

    by_name = {item["name"]: item["usage"] for item in flushed_spans(tracer, ingest)}
    assert by_name["reads-only"] == {"input_tokens": 120, "output_tokens": 30, "cached_tokens": 100}
    assert by_name["writes-5m"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "cache_write_tokens": 20,
    }
    assert by_name["writes-split"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "cached_tokens": 50,
        "cache_write_tokens": 20,
        "cache_write_1h_tokens": 5,
    }


def test_set_usage_tier_and_search_in_payload(tracer, ingest):
    with tracer.span("searched", kind="llm") as span:
        span.set_model("claude-sonnet-4-5", provider="anthropic")
        span.set_usage(
            input_tokens=120,
            output_tokens=30,
            service_tier="priority_on_demand",
            web_search_requests=3,
        )
        assert span.service_tier == "priority"
        assert span.web_search_requests == 3

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["usage"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "service_tier": "priority",
        "server_tool_use": {"web_search_requests": 3},
    }
    assert exported["attributes"] == {}


def test_unknown_tier_goes_to_raw_attribute(tracer, ingest):
    with tracer.span("odd-tier", kind="llm") as span:
        span.set_model("gpt-4.1", provider="openai")
        span.set_usage(input_tokens=10, output_tokens=5, service_tier="turbo")
        assert span.service_tier is None

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert exported["attributes"] == {"usage.service_tier_raw": "turbo"}


def test_unknown_tier_spelling_is_cut_to_64_characters(tracer, ingest):
    """The server keeps 64 characters of an unknown spelling; the SDK sends no more."""
    with tracer.span("long-tier", kind="llm") as span:
        span.set_model("gpt-4.1", provider="openai")
        span.set_usage(input_tokens=10, output_tokens=5, service_tier="t" * 200)

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["attributes"] == {"usage.service_tier_raw": "t" * 64}


def test_a_batch_item_tier_spelling_is_cut_to_64_characters(tracer, ingest):
    from spanlight.integrations._batch import _record_usage

    with tracer.span("batch-item", kind="llm") as span:
        _record_usage(span, {"input_tokens": 10, "output_tokens": 5, "service_tier": "s" * 100})

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["usage"]["service_tier"] == "batch"
    assert exported["attributes"] == {"usage.service_tier_raw": "s" * 64}


def test_known_tier_after_unknown_tier_drops_raw_attribute(tracer, ingest):
    with tracer.span("tier-corrected", kind="llm") as span:
        span.set_model("gpt-4.1", provider="openai")
        span.set_usage(input_tokens=10, output_tokens=5, service_tier="turbo")
        span.set_usage(service_tier="priority")
        assert span.service_tier == "priority"

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["usage"]["service_tier"] == "priority"
    assert "usage.service_tier_raw" not in exported["attributes"]


def test_blank_tier_is_ignored(tracer, ingest):
    with tracer.span("blank-tier", kind="llm") as span:
        span.set_usage(input_tokens=10, output_tokens=5, service_tier="  ")
        assert span.service_tier is None

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert exported["attributes"] == {}


def test_clear_output_tokens_after_set(tracer, ingest):
    with tracer.span("cut-stream", kind="llm") as span:
        span.set_usage(input_tokens=80, output_tokens=12, cached_tokens=40)
        span.clear_output_tokens()
        assert span.output_tokens is None

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["usage"] == {"input_tokens": 80, "cached_tokens": 40}


def test_clear_output_tokens_after_end_is_ignored(tracer, ingest):
    with tracer.span("done", kind="llm") as span:
        span.set_usage(input_tokens=80, output_tokens=12)
    span.clear_output_tokens()

    assert flushed_spans(tracer, ingest)[0]["usage"] == {"input_tokens": 80, "output_tokens": 12}


def test_payload_without_new_fields_unchanged(tracer, ingest):
    with tracer.span("plain", kind="llm") as span:
        span.set_usage(input_tokens=120, output_tokens=30)
    with tracer.span("no-usage", kind="llm"):
        pass

    by_name = {item["name"]: item for item in flushed_spans(tracer, ingest)}
    assert by_name["plain"]["usage"] == {"input_tokens": 120, "output_tokens": 30}
    assert by_name["plain"]["attributes"] == {}
    assert by_name["no-usage"]["usage"] is None


def test_span_records_error_and_reraises(tracer, ingest):
    with pytest.raises(ZeroDivisionError), tracer.span("divide"):
        _ = 1 / 0

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["status"] == "error"
    assert exported["status_message"] == "division by zero"


def test_manual_record_error_with_message(tracer, ingest):
    with tracer.span("validate") as span:
        span.record_error("schema mismatch")

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["status"] == "error"
    assert exported["status_message"] == "schema mismatch"


def test_setters_after_end_are_ignored(tracer, ingest):
    with tracer.span("done") as span:
        span.set_output("final")
    span.set_output("too late")
    span.end()

    exported = flushed_spans(tracer, ingest)
    assert len(exported) == 1
    assert exported[0]["output"] == "final"


def test_nested_spans_restore_the_previous_current_span(tracer, ingest):
    assert spanlight.get_current_span() is None
    with tracer.span("outer") as outer:
        with tracer.span("inner") as inner:
            assert spanlight.get_current_span() is inner
            assert spanlight.get_current_trace_id() == outer.trace_id
        assert spanlight.get_current_span() is outer
    assert spanlight.get_current_span() is None

    flushed_spans(tracer, ingest)
    assert ingest.span_named("inner")["parent_span_id"] == ingest.span_named("outer")["span_id"]


async def test_async_with_span(tracer, ingest):
    async with tracer.span("async-block", kind="http") as span:
        await asyncio.sleep(0)
        span.set_output({"status": 200})

    exported = flushed_spans(tracer, ingest)[0]
    assert exported["kind"] == "http"
    assert exported["output"] == {"status": 200}


def test_unknown_kind_falls_back_to_other(tracer, ingest):
    with tracer.span("weird", kind="banana"):  # type: ignore[arg-type]
        pass

    assert flushed_spans(tracer, ingest)[0]["kind"] == "other"


def test_module_level_span_uses_global_client(global_tracer, ingest):
    with spanlight.span("global-block"):
        pass

    assert flushed_spans(global_tracer, ingest)[0]["name"] == "global-block"


# ---------------------------------------------------------------------- #
# update_trace
# ---------------------------------------------------------------------- #


def test_update_trace_applies_to_all_spans_of_the_trace(tracer, ingest):
    @tracer.observe
    def tag_from_child() -> None:
        spanlight.update_trace(tags=["beta"], session_id="s_1")

    with tracer.span("request"):
        spanlight.update_trace(name="support-request", user_id="u_42", tags=["vip"])
        tag_from_child()

    flushed_spans(tracer, ingest)
    root_trace = ingest.span_named("request")["trace"]
    assert root_trace == {
        "name": "support-request",
        "environment": "test",
        "release": "v0.0.1",
        "user_id": "u_42",
        "session_id": "s_1",
        "tags": ["vip", "beta"],
    }


def test_update_trace_outside_a_span_is_a_no_op():
    spanlight.update_trace(user_id="nobody")  # must not raise


def test_client_update_trace_method(tracer, ingest):
    with tracer.span("root"):
        tracer.update_trace(user_id="u_1")

    assert flushed_spans(tracer, ingest)[0]["trace"]["user_id"] == "u_1"


# ---------------------------------------------------------------------- #
# Context propagation
# ---------------------------------------------------------------------- #


async def test_asyncio_tasks_inherit_the_current_span(tracer, ingest):
    @tracer.observe(kind="tool")
    async def lookup(key: str) -> str:
        await asyncio.sleep(0.01)
        return key.upper()

    async with tracer.span("fan-out") as parent:
        results = await asyncio.gather(lookup("a"), lookup("b"), lookup("c"))
        task = asyncio.create_task(lookup("d"))
        results.append(await task)

    assert results == ["A", "B", "C", "D"]
    flushed_spans(tracer, ingest)
    lookups = [span for span in ingest.spans if span["name"] == "lookup"]
    assert len(lookups) == 4
    assert {span["parent_span_id"] for span in lookups} == {parent.span_id}
    assert {span["trace_id"] for span in lookups} == {parent.trace_id}


async def test_concurrent_tasks_do_not_see_each_others_spans(tracer, ingest):
    both_started = asyncio.Event()
    started = 0

    async def worker(name: str) -> str | None:
        nonlocal started
        async with tracer.span(name):
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            current = spanlight.get_current_span()
            return current.name if current else None

    names = await asyncio.gather(worker("task-a"), worker("task-b"))

    assert names == ["task-a", "task-b"]
    flushed_spans(tracer, ingest)
    first = ingest.span_named("task-a")
    second = ingest.span_named("task-b")
    assert first["parent_span_id"] is None
    assert second["parent_span_id"] is None
    assert first["trace_id"] != second["trace_id"]


def test_threads_need_an_explicit_context_copy(tracer, ingest):
    seen: dict[str, str | None] = {}

    def record(label: str) -> None:
        current = spanlight.get_current_span()
        seen[label] = current.name if current else None

    with tracer.span("main"):
        plain = threading.Thread(target=record, args=("plain",))
        context = contextvars.copy_context()
        copied = threading.Thread(target=context.run, args=(record, "copied"))
        plain.start()
        copied.start()
        plain.join()
        copied.join()

    assert seen == {"plain": None, "copied": "main"}


def test_a_200_kb_input_is_sent_whole_and_a_cut_attribute_flags_the_span(tracer, ingest):
    context = "retrieved passage " * 12_000  # about 216 KB, one string
    with tracer.span("rag", kind="llm") as span:
        span.set_input({"messages": [{"role": "user", "content": context}]})
    with tracer.span("long-attribute") as span:
        span.set_attribute("note", "x" * 40_000)

    by_name = {item["name"]: item for item in flushed_spans(tracer, ingest)}
    assert by_name["rag"]["input"]["messages"][0]["content"] == context
    assert "truncated" not in by_name["rag"]
    assert len(by_name["long-attribute"]["attributes"]["note"]) < 40_000
    assert by_name["long-attribute"]["truncated"] is True
