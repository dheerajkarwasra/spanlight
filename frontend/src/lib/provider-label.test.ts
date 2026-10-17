import { describe, expect, it } from "vitest";

import { feedLabel, providerLabel } from "./provider-label";

describe("providerLabel", () => {
  it("names known providers whatever their case and shows other slugs as sent", () => {
    expect(providerLabel("anthropic")).toBe("Anthropic");
    expect(providerLabel("OpenAI")).toBe("OpenAI");
    expect(providerLabel("openai_compatible")).toBe("OpenAI-compatible");
    expect(providerLabel("gemini")).toBe("Google Gemini");
    expect(providerLabel("bedrock")).toBe("Amazon Bedrock");
    expect(providerLabel("acme")).toBe("acme");
  });

  it("says Unknown provider for a call that named none", () => {
    expect(providerLabel(null)).toBe("Unknown provider");
    expect(providerLabel("  ")).toBe("Unknown provider");
  });
});

describe("feedLabel", () => {
  it("names Google's feed after the Google Cloud status page", () => {
    expect(feedLabel("google")).toBe("Google Cloud");
    expect(feedLabel("openai")).toBe("OpenAI");
  });
});
