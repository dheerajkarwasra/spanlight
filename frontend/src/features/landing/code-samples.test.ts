import { describe, expect, it } from "vitest";

import {
  CODE_SAMPLE_IDS,
  CODE_SAMPLES,
  codeToText,
  INSTALL_COMMAND,
  isCodeSampleId,
  type CodeSample,
} from "./code-samples";

function sample(id: CodeSample["id"]): CodeSample {
  return CODE_SAMPLES[id];
}

describe("code samples", () => {
  it("offers the Python SDK, OpenTelemetry and Gateway options in order", () => {
    expect(CODE_SAMPLE_IDS).toEqual(["python", "otlp", "gateway"]);
    expect(CODE_SAMPLE_IDS.map((id) => CODE_SAMPLES[id].label)).toEqual([
      "Python SDK",
      "OpenTelemetry",
      "Gateway",
    ]);
    expect(isCodeSampleId("otlp")).toBe(true);
    expect(isCodeSampleId("gateway")).toBe(true);
    expect(isCodeSampleId("curl")).toBe(false);
    expect(isCodeSampleId("toString")).toBe(false);
  });

  it("rebuilds the README Python snippet as plain text for the clipboard", () => {
    const text = codeToText(sample("python").lines);
    expect(text.split("\n")).toHaveLength(14);
    expect(text).toContain("import spanlight\nfrom anthropic import Anthropic\n\n");
    expect(text).toContain('spanlight.init(api_key="spl_live_…", host="http://localhost:8080")');
    expect(text).toContain("client = spanlight.wrap_anthropic(Anthropic())");
    expect(text).toContain("@spanlight.observe()\ndef answer(question: str) -> str:");
    expect(text.endsWith("    return reply.content[0].text")).toBe(true);
    expect(INSTALL_COMMAND).toBe('pip install "spanlight[anthropic]"');
  });

  it("points the OTLP exporter at the ingestion endpoint with a key placeholder", () => {
    const text = codeToText(sample("otlp").lines);
    expect(text).toContain("/v1/otlp/traces");
    expect(text).toContain("Authorization=Bearer%20<YOUR_API_KEY>");
  });

  it("shows the no-SDK gateway sample with a gateway key placeholder, not an ingest key", () => {
    const text = codeToText(sample("gateway").lines);
    expect(text).toContain("/gw/v1");
    expect(text).toContain("<YOUR_GATEWAY_KEY>");
    expect(text).not.toContain("import spanlight");
    expect(text).not.toContain("<YOUR_API_KEY>");
  });
});
