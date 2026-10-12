import { describe, expect, it } from "vitest";

import {
  CODE_SAMPLE_IDS,
  CODE_SAMPLES,
  codeToText,
  isCodeSampleId,
  visibleLines,
  type CodeSample,
} from "./code-samples";

function sample(id: CodeSample["id"]): CodeSample {
  return CODE_SAMPLES[id];
}

describe("code samples", () => {
  it("offers Python, OpenAI, OTLP and Gateway tabs in order", () => {
    expect(CODE_SAMPLE_IDS.map((id) => CODE_SAMPLES[id].label)).toEqual([
      "Python",
      "OpenAI",
      "OTLP",
      "Gateway",
    ]);
    expect(isCodeSampleId("otlp")).toBe(true);
    expect(isCodeSampleId("gateway")).toBe(true);
    expect(isCodeSampleId("curl")).toBe(false);
    expect(isCodeSampleId("toString")).toBe(false);
  });

  it("rebuilds the Python snippet as plain text for the clipboard", () => {
    const text = codeToText(sample("python").lines);
    expect(text.split("\n")).toHaveLength(15);
    expect(text).toContain("import spanlight\nfrom anthropic import Anthropic\n\n");
    expect(text).toContain('spanlight.init(environment="production")');
    expect(text).toContain('@spanlight.observe(name="answer_ticket")');
    expect(text).toContain("def answer_ticket(ticket_id: str, question: str) -> str:");
    expect(text.endsWith("    return reply.content[0].text")).toBe(true);
  });

  it("uses the compact snippet on phones when there is one", () => {
    const python = sample("python");
    expect(codeToText(visibleLines(python, true))).toBe(
      [
        "import spanlight",
        "from anthropic import Anthropic",
        "",
        "spanlight.init()",
        "client = spanlight.wrap_anthropic(",
        "    Anthropic()",
        ")",
      ].join("\n"),
    );
    expect(visibleLines(python, false)).toBe(python.lines);

    const otlp = sample("otlp");
    expect(visibleLines(otlp, true)).toBe(otlp.lines);
  });

  it("points the OTLP exporter at the ingestion endpoint with a key placeholder", () => {
    const text = codeToText(sample("otlp").lines);
    expect(text).toContain("/v1/otlp/traces");
    expect(text).toContain("Authorization=Bearer%20<YOUR_API_KEY>");
  });

  it("shows the no-SDK gateway sample with a gateway key placeholder, not an ingest key", () => {
    const gateway = sample("gateway");
    const text = codeToText(gateway.lines);
    expect(text).toContain("/gw/v1");
    expect(text).toContain("<YOUR_GATEWAY_KEY>");
    expect(text).not.toContain("import spanlight");
    expect(text).not.toContain("<YOUR_API_KEY>");
    expect(gateway.compactLines).toBeDefined();
    expect(codeToText(visibleLines(gateway, true))).toContain("<YOUR_GATEWAY_KEY>");
  });
});
