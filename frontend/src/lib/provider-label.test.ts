import { describe, expect, it } from "vitest";

import { feedLabel, NO_PROVIDER_REASON, providerLabel } from "./provider-label";

describe("providerLabel", () => {
  it("names known providers whatever their case and shows other slugs as sent", () => {
    expect(providerLabel("anthropic")).toBe("Anthropic");
    expect(providerLabel("OpenAI")).toBe("OpenAI");
    expect(providerLabel("openai_compatible")).toBe("OpenAI-compatible");
    expect(providerLabel("gemini")).toBe("Google Gemini");
    expect(providerLabel("bedrock")).toBe("Amazon Bedrock");
    expect(providerLabel("acme")).toBe("acme");
  });

  it("says No provider for a call that named none", () => {
    expect(providerLabel(null)).toBe("No provider");
    expect(providerLabel("  ")).toBe("No provider");
  });

  it("names every cause a row can have no provider", () => {
    expect(NO_PROVIDER_REASON).toBe(
      "Gateway cache hits, gateway refusals and spans sent without a provider",
    );
  });
});

describe("feedLabel", () => {
  it("names Google's feed after the Google Cloud status page", () => {
    expect(feedLabel("google")).toBe("Google Cloud");
    expect(feedLabel("openai")).toBe("OpenAI");
  });
});
