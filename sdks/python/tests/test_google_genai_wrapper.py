"""Gemini SDK calls with the official client and an in-process HTTP transport."""

import json
from pathlib import Path

import httpx
import pytest
from google import genai
from google.genai import errors, types
from support import flushed_spans

import spanlight

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures/gemini-generate-content.json").read_text()
)
MODEL = FIXTURE["request"]["model"]
CONFIG = types.GenerateContentConfig(
    system_instruction="Answer briefly", max_output_tokens=40, temperature=0
)
ERROR = {"error": {"code": 400, "message": "Request declined", "status": "INVALID_ARGUMENT"}}


def make_client(respond):
    transport = httpx.MockTransport(respond)
    return genai.Client(
        api_key="test-gemini-key",
        http_options=types.HttpOptions(
            client_args={"transport": transport}, async_client_args={"transport": transport}
        ),
    )


def stream_answer(_request):
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in FIXTURE["chunks"])
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def test_native_unary_has_replayable_input(tracer, ingest):
    requests = []

    def reply(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=FIXTURE["response"])

    with make_client(reply) as client:
        assert spanlight.wrap_google_genai(client, spanlight=tracer) is client
        spanlight.wrap_google_genai(client, spanlight=tracer)
        result = client.models.generate_content(model=MODEL, contents="Hello", config=CONFIG)

    assert result.text == "Hello world"
    [span] = flushed_spans(tracer, ingest)
    assert span["provider"] == "google" and span["model"] == MODEL
    assert span["attributes"]["spanlight.gateway.surface"] == "generate_content"
    assert span["attributes"]["response.id"] == "gemini-test"
    assert span["finish_reason"] == "STOP" and span["usage"] == FIXTURE["usage"]
    assert span["input"] == FIXTURE["request"]
    assert requests == [{key: val for key, val in span["input"].items() if key != "model"}]
    assert span["output"] == FIXTURE["response"]["candidates"][0]["content"]


@pytest.mark.parametrize("early", [False, True])
def test_sync_stream_keeps_final_usage_only_and_closes(tracer, ingest, early):
    with make_client(stream_answer) as client:
        spanlight.wrap_google_genai(client, spanlight=tracer)
        stream = client.models.generate_content_stream(model=MODEL, contents="Hello")
        if early:
            assert next(stream).text == "Hello"
            stream.close()
        else:
            assert "".join(chunk.text or "" for chunk in stream) == "Hello world"

    [span] = flushed_spans(tracer, ingest)
    assert span["time_to_first_token_ms"] is not None
    if early:
        assert span["usage"].get("output_tokens") is None
        assert span["attributes"]["usage.unreported_reason"] == "client_disconnected"
    else:
        assert span["usage"] == FIXTURE["usage"]
        assert span["finish_reason"] == "STOP"


@pytest.mark.parametrize("early", [False, True])
async def test_async_stream_on_aio_uses_native_aclose(tracer, ingest, early):
    client = make_client(stream_answer)
    try:
        async with client.aio as aio:
            assert spanlight.wrap_google_genai(aio, spanlight=tracer) is aio
            stream = await aio.models.generate_content_stream(model=MODEL, contents="Hello")
            if early:
                assert (await anext(stream)).text == "Hello"
                await stream.aclose()
            else:
                assert [chunk.text async for chunk in stream] == ["Hello", " world"]
    finally:
        client.close()

    [span] = flushed_spans(tracer, ingest)
    assert span["usage"].get("output_tokens") == (None if early else 7)


async def test_async_unary_preserves_the_google_error(tracer, ingest):
    client = make_client(lambda _: httpx.Response(400, json=ERROR))
    try:
        spanlight.wrap_google_genai(client, spanlight=tracer)
        async with client.aio as aio:
            with pytest.raises(errors.ClientError, match="Request declined") as caught:
                await aio.models.generate_content(model=MODEL, contents="Hello")
    finally:
        client.close()

    assert caught.value.code == 400
    [span] = flushed_spans(tracer, ingest)
    assert span["status"] == "error"
    assert span["attributes"]["http.status_code"] == 400


async def test_async_unary_passes_transport_options_without_recording_them(tracer, ingest):
    seen = []

    def reply(request):
        seen.append(request.headers["x-private-example"])
        return httpx.Response(200, json=FIXTURE["response"])

    client = make_client(reply)
    try:
        spanlight.wrap_google_genai(client, spanlight=tracer, provider="vertex_ai")
        async with client.aio as aio:
            response = await aio.models.generate_content(
                model=MODEL,
                contents=types.Content(role="user", parts=[types.Part(text="Hello")]),
                config=types.GenerateContentConfig(
                    http_options=types.HttpOptions(headers={"x-private-example": "test-secret"}),
                ),
            )
    finally:
        client.close()

    assert response.text == "Hello world"
    assert seen == ["test-secret"]
    [span] = flushed_spans(tracer, ingest)
    assert span["provider"] == "vertex_ai"
    assert span["input"] == {"model": MODEL, "contents": FIXTURE["request"]["contents"]}
