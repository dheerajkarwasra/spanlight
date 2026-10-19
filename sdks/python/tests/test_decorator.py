"""Semantics of ``@observe``: nesting, async, generators, errors and capture flags."""

from __future__ import annotations

import asyncio
import re

import pytest
from support import flushed_spans

import spanlight

TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID = re.compile(r"^[0-9a-f]{16}$")


class LookupError_(Exception):
    """A user exception type, to prove it propagates unchanged."""


# ---------------------------------------------------------------------- #
# Plain functions
# ---------------------------------------------------------------------- #


def test_nested_functions_form_one_trace(tracer, ingest):
    @tracer.observe(kind="retrieval")
    def retrieve(query: str) -> list[str]:
        return [f"doc about {query}"]

    @tracer.observe
    def answer(question: str, *, verbose: bool = False) -> str:
        docs = retrieve(question)
        return f"{len(docs)} docs"

    assert answer("refunds", verbose=True) == "1 docs"

    spans = flushed_spans(tracer, ingest)
    root = ingest.span_named("answer")
    child = ingest.span_named("retrieve")
    assert len(spans) == 2

    assert TRACE_ID.match(root["trace_id"])
    assert SPAN_ID.match(root["span_id"])
    assert root["parent_span_id"] is None
    assert child["trace_id"] == root["trace_id"]
    assert child["parent_span_id"] == root["span_id"]

    assert root["kind"] == "chain"
    assert child["kind"] == "retrieval"
    assert root["status"] == "ok"
    assert root["input"] == {"question": "refunds", "verbose": True}
    assert root["output"] == "1 docs"
    assert child["output"] == ["doc about refunds"]
    assert root["trace"]["name"] == "answer"
    assert root["trace"]["environment"] == "test"
    assert root["trace"]["release"] == "v0.0.1"
    assert root["start_time"] <= child["start_time"] <= child["end_time"] <= root["end_time"]


def test_sibling_calls_start_separate_traces(tracer, ingest):
    @tracer.observe
    def step() -> None:
        return None

    step()
    step()

    first, second = flushed_spans(tracer, ingest)
    assert first["trace_id"] != second["trace_id"]


def test_method_input_omits_self(tracer, ingest):
    class Bot:
        @tracer.observe(name="bot.reply")
        def reply(self, message: str) -> str:
            return message.upper()

    assert Bot().reply("hi") == "HI"

    span = flushed_spans(tracer, ingest)[0]
    assert span["name"] == "bot.reply"
    assert span["input"] == {"message": "hi"}


def test_exception_marks_span_failed_and_propagates_unchanged(tracer, ingest):
    error = LookupError_("customer not found")

    @tracer.observe
    def lookup() -> None:
        raise error

    with pytest.raises(LookupError_) as raised:
        lookup()

    assert raised.value is error
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert span["status_message"] == "customer not found"
    assert span["attributes"]["error.type"] == "LookupError_"
    assert span["output"] is None


def test_capture_flags_disable_input_and_output(tracer, ingest):
    @tracer.observe(capture_input=False, capture_output=False)
    def secret(password: str) -> str:
        return password[::-1]

    secret("hunter2")

    span = flushed_spans(tracer, ingest)[0]
    assert span["input"] is None
    assert span["output"] is None


def test_positional_name_and_module_level_decorator(global_tracer, ingest):
    @spanlight.observe("custom-name")
    def first() -> int:
        return 1

    @spanlight.observe(name="keyword-name", kind="tool")
    def second() -> int:
        return 2

    assert first() + second() == 3

    flushed_spans(global_tracer, ingest)
    assert ingest.span_named("custom-name")["kind"] == "chain"
    assert ingest.span_named("keyword-name")["kind"] == "tool"


def test_decorator_resolves_global_client_at_call_time(ingest):
    @spanlight.observe
    def defined_before_init() -> str:
        return "ok"

    tracer = spanlight.init(
        api_key="key", host="http://spanlight.test", transport=ingest.transport()
    )
    assert defined_before_init() == "ok"

    assert flushed_spans(tracer, ingest)[0]["name"] == "defined_before_init"


def test_sdk_failure_never_breaks_the_decorated_function(tracer, ingest, monkeypatch):
    def broken_start_span(*args: object, **kwargs: object) -> None:
        raise RuntimeError("sdk bug")

    monkeypatch.setattr(tracer, "start_span", broken_start_span)

    @tracer.observe
    def business_logic(value: int) -> int:
        return value * 2

    assert business_logic(21) == 42
    assert flushed_spans(tracer, ingest) == []


def test_unserializable_values_are_recorded_safely(tracer, ingest):
    class Opaque:
        def __repr__(self) -> str:
            return "<Opaque>"

    @tracer.observe
    def handle(thing: object) -> dict[str, object]:
        return {"thing": thing, "raw": b"\x00\x01", "nan": float("nan")}

    handle(Opaque())

    span = flushed_spans(tracer, ingest)[0]
    assert span["input"] == {"thing": "<Opaque>"}
    assert span["output"] == {"thing": "<Opaque>", "raw": "<2 bytes>", "nan": "nan"}


# ---------------------------------------------------------------------- #
# Async functions
# ---------------------------------------------------------------------- #


async def test_async_functions_nest(tracer, ingest):
    @tracer.observe(kind="tool")
    async def fetch(order_id: str) -> dict[str, str]:
        return {"id": order_id, "status": "shipped"}

    @tracer.observe
    async def handle(order_id: str) -> str:
        order = await fetch(order_id)
        return order["status"]

    assert await handle("o_1") == "shipped"

    flushed_spans(tracer, ingest)
    root = ingest.span_named("handle")
    child = ingest.span_named("fetch")
    assert child["parent_span_id"] == root["span_id"]
    assert root["output"] == "shipped"
    assert child["output"] == {"id": "o_1", "status": "shipped"}


async def test_async_exception_propagates(tracer, ingest):
    @tracer.observe
    async def explode() -> None:
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        await explode()

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert span["status_message"] == "bad input"


# ---------------------------------------------------------------------- #
# Generators
# ---------------------------------------------------------------------- #


def test_generator_span_ends_when_exhausted(tracer, ingest):
    @tracer.observe(kind="llm")
    def stream_tokens(prompt: str):
        yield "Hel"
        yield "lo"

    generator = stream_tokens("greet")
    first = next(generator)
    assert first == "Hel"
    assert tracer.flush(timeout=5)
    assert ingest.spans == []  # not ended while items remain

    assert list(generator) == ["lo"]
    span = flushed_spans(tracer, ingest)[0]
    assert span["name"] == "stream_tokens"
    assert span["input"] == {"prompt": "greet"}
    assert span["output"] == "Hello"
    assert span["status"] == "ok"


def test_generator_context_is_active_only_inside_the_body(tracer, ingest):
    seen_inside: list[str | None] = []
    seen_outside: list[str | None] = []

    @tracer.observe
    def child() -> None:
        return None

    @tracer.observe
    def produce():
        for index in range(2):
            seen_inside.append(spanlight.get_current_span().name)
            child()
            yield index

    with tracer.span("consumer"):
        for _ in produce():
            seen_outside.append(spanlight.get_current_span().name)

    assert seen_inside == ["produce", "produce"]
    assert seen_outside == ["consumer", "consumer"]

    flushed_spans(tracer, ingest)
    producer = ingest.span_named("produce")
    consumer = ingest.span_named("consumer")
    children = [span for span in ingest.spans if span["name"] == "child"]
    assert producer["parent_span_id"] == consumer["span_id"]
    assert [span["parent_span_id"] for span in children] == [producer["span_id"]] * 2
    assert producer["output"] == [0, 1]


def test_generator_closed_early_still_ends_span(tracer, ingest):
    @tracer.observe
    def numbers():
        yield from range(100)

    for number in numbers():
        if number == 2:
            break

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["output"] == [0, 1, 2]


def test_generator_exception_is_recorded(tracer, ingest):
    @tracer.observe
    def failing():
        yield 1
        raise KeyError("missing")

    with pytest.raises(KeyError):
        list(failing())

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert span["attributes"]["error.type"] == "KeyError"


def test_generator_send_and_return_value_pass_through(tracer, ingest):
    @tracer.observe
    def accumulator():
        total = 0
        while True:
            value = yield total
            if value is None:
                return total
            total += value

    generator = accumulator()
    assert next(generator) == 0
    assert generator.send(5) == 5
    assert generator.send(10) == 15
    with pytest.raises(StopIteration) as stop:
        generator.send(None)
    assert stop.value.value == 15

    span = flushed_spans(tracer, ingest)[0]
    assert span["output"] == [0, 5, 15]


def test_generator_throw_is_forwarded(tracer, ingest):
    @tracer.observe
    def resilient():
        try:
            yield "working"
        except ValueError:
            yield "recovered"

    generator = resilient()
    assert next(generator) == "working"
    assert generator.throw(ValueError("hiccup")) == "recovered"
    generator.close()

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["output"] == "workingrecovered"


# ---------------------------------------------------------------------- #
# Async generators
# ---------------------------------------------------------------------- #


async def test_async_generator_span_ends_when_exhausted(tracer, ingest):
    @tracer.observe
    async def child() -> None:
        return None

    @tracer.observe(name="stream")
    async def stream_words():
        for word in ["a", "b", "c"]:
            await child()
            yield word

    words = [word async for word in stream_words()]
    assert words == ["a", "b", "c"]

    flushed_spans(tracer, ingest)
    stream = ingest.span_named("stream")
    children = [span for span in ingest.spans if span["name"] == "child"]
    assert stream["output"] == "abc"
    assert len(children) == 3
    assert all(span["parent_span_id"] == stream["span_id"] for span in children)


async def test_async_generator_error_and_early_close(tracer, ingest):
    @tracer.observe(name="broken")
    async def broken():
        yield 1
        raise RuntimeError("stream died")

    @tracer.observe(name="endless")
    async def endless():
        index = 0
        while True:
            yield index
            index += 1

    with pytest.raises(RuntimeError, match="stream died"):
        async for _ in broken():
            pass

    generator = endless()
    async for value in generator:
        if value == 1:
            break
    await generator.aclose()

    flushed_spans(tracer, ingest)
    assert ingest.span_named("broken")["status"] == "error"
    assert ingest.span_named("endless")["status"] == "ok"
    assert ingest.span_named("endless")["output"] == [0, 1]


def test_async_generator_abandoned_by_break_is_not_an_error(tracer, ingest):
    @tracer.observe(name="abandoned")
    async def numbers():
        for index in range(10):
            yield index

    async def main() -> None:
        async for value in numbers():
            if value == 1:
                break

    # asyncio closes the abandoned generator by cancelling its `aclose()` at loop shutdown.
    asyncio.run(main())

    flushed_spans(tracer, ingest)
    assert ingest.span_named("abandoned")["status"] == "ok"


# ---------------------------------------------------------------------- #
# Disabled mode
# ---------------------------------------------------------------------- #


def test_disabled_client_is_a_no_op(ingest):
    tracer = spanlight.Spanlight(api_key="key", enabled=False, transport=ingest.transport())

    @tracer.observe
    def compute() -> int:
        return 7

    @tracer.observe
    def produce():
        yield 1

    assert compute() == 7
    assert list(produce()) == [1]
    with tracer.span("block") as span:
        span.set_output("ignored")
        assert span.recording is False

    assert tracer.enabled is False
    assert tracer.exporter is None
    assert tracer.flush() is True
    assert ingest.requests == []


def test_missing_api_key_disables_with_warning(caplog):
    with caplog.at_level("WARNING", logger="spanlight"):
        tracer = spanlight.Spanlight()
    assert tracer.enabled is False
    assert "no API key configured" in caplog.text


def test_environment_variables_configure_the_client(monkeypatch):
    monkeypatch.setenv("SPANLIGHT_API_KEY", "spl_live_env")
    monkeypatch.setenv("SPANLIGHT_HOST", "https://spanlight.example.com/")
    monkeypatch.setenv("SPANLIGHT_ENVIRONMENT", "staging")
    monkeypatch.setenv("SPANLIGHT_RELEASE", "abc123")

    tracer = spanlight.Spanlight()
    try:
        assert tracer.enabled is True
        assert tracer.config.api_key == "spl_live_env"
        assert tracer.config.ingest_url == "https://spanlight.example.com/v1/traces"
        assert tracer.config.environment == "staging"
        assert tracer.config.release == "abc123"
    finally:
        tracer.shutdown()

    monkeypatch.setenv("SPANLIGHT_ENABLED", "false")
    assert spanlight.Spanlight().enabled is False
