import { readFileSync } from "node:fs";

import { ApiError, GoogleGenAI } from "@google/genai";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as spanlight from "../src/index.js";
import { captureSpanlight, jsonResponse, mockProvider, sseResponse } from "./support.js";

const fixture = JSON.parse(
  readFileSync(new URL("../../fixtures/gemini-generate-content.json", import.meta.url), "utf8"),
) as {
  request: { model: string } & Record<string, unknown>;
  response: { candidates: { content: unknown }[] };
  chunks: unknown[];
  usage: Record<string, number>;
};
const model = fixture.request.model;
const params = { model, contents: "Hello" };

function google() {
  return spanlight.wrapGoogleGenAI(new GoogleGenAI({ apiKey: "test-gemini-key" }));
}

afterEach(() => vi.unstubAllGlobals());

describe("wrapGoogleGenAI with the real Google client", () => {
  it("records native replay input and wraps a client once", async () => {
    const capture = captureSpanlight();
    const transport = mockProvider(() => jsonResponse(fixture.response));
    vi.stubGlobal("fetch", transport.fetch);
    const client = google();
    expect(spanlight.wrapGoogleGenAI(client)).toBe(client);
    const answer = await client.models.generateContent({
      ...params,
      config: { systemInstruction: "Answer briefly", maxOutputTokens: 40, temperature: 0 },
    });
    expect(answer.text).toBe("Hello world");
    await spanlight.flush();

    const { model: _model, ...body } = fixture.request;
    expect(transport.requests).toEqual([body]);
    expect(capture.spans()).toHaveLength(1);
    expect(capture.spans()[0]).toMatchObject({
      provider: "google",
      model,
      input: fixture.request,
      output: fixture.response.candidates[0]?.content,
      usage: fixture.usage,
      finish_reason: "STOP",
      attributes: {
        "spanlight.gateway.surface": "generate_content",
        "response.id": "gemini-test",
      },
    });
  });

  it.each([false, true])("preserves chunks and handles early close %s", async (early) => {
    const capture = captureSpanlight();
    const transport = mockProvider(() => sseResponse(fixture.chunks.map((chunk) => [null, chunk])));
    vi.stubGlobal("fetch", transport.fetch);
    const stream = await google().models.generateContentStream(params);
    const text: (string | undefined)[] = [];
    for await (const chunk of stream) {
      text.push(chunk.text);
      if (early) break;
    }
    expect(text).toEqual(early ? ["Hello"] : ["Hello", " world"]);
    await spanlight.flush();

    expect(capture.spans()).toHaveLength(1);
    const span = capture.spans()[0] ?? {};
    expect(span.time_to_first_token_ms).toEqual(expect.any(Number));
    if (early) {
      expect(span.usage).toMatchObject({ input_tokens: 32, cached_tokens: 12 });
      expect(span.usage).not.toHaveProperty("output_tokens");
      expect(span.attributes).toMatchObject({ "usage.unreported_reason": "client_disconnected" });
    } else {
      expect(span.usage).toEqual(fixture.usage);
      expect(span.finish_reason).toBe("STOP");
    }
  });

  it("preserves the provider's API error", async () => {
    const capture = captureSpanlight();
    const response = {
      error: { code: 400, message: "Request declined", status: "INVALID_ARGUMENT" },
    };
    vi.stubGlobal("fetch", mockProvider(() => jsonResponse(response, 400)).fetch);
    await expect(google().models.generateContent(params)).rejects.toBeInstanceOf(ApiError);
    await spanlight.flush();
    expect(capture.spans()).toHaveLength(1);
    expect(capture.spans()[0]).toMatchObject({
      provider: "google",
      status: "error",
      attributes: { "http.status_code": 400 },
    });
  });

  it("closes the stream before the first chunk and keeps an explicit provider", async () => {
    const capture = captureSpanlight();
    vi.stubGlobal("fetch", mockProvider(() => sseResponse([[null, fixture.chunks[0]]])).fetch);
    const client = spanlight.wrapGoogleGenAI(new GoogleGenAI({ apiKey: "test-gemini-key" }), {
      provider: "vertex_ai",
    });
    const stream = await client.models.generateContentStream(params);
    expect((await stream.return(undefined)).done).toBe(true);
    await spanlight.flush();
    expect(capture.spans()).toHaveLength(1);
    expect(capture.spans()[0]).toMatchObject({
      provider: "vertex_ai",
      attributes: { "usage.unreported_reason": "client_disconnected" },
    });
  });
});
