import { describe, expect, it } from "vitest";

import { sdkSnippet } from "./key-snippets";

describe("sdkSnippet", () => {
  it("points the SDK at this origin's gateway when it is embedded", () => {
    const origin = window.location.origin;
    expect(sdkSnippet("openai", "embedded")).toContain(`base_url="${origin}/gw/v1"`);
    expect(sdkSnippet("anthropic", "embedded")).toContain(`base_url="${origin}/gw"`);
  });

  it.each(["standalone", "disabled"] as const)(
    "uses a placeholder, never this origin, when the gateway is %s",
    (mode) => {
      const openai = sdkSnippet("openai", mode);
      expect(openai).toContain('base_url="<your gateway URL>/gw/v1"');
      expect(openai).not.toContain(window.location.origin);
      expect(sdkSnippet("anthropic", mode)).toContain('base_url="<your gateway URL>/gw"');
    },
  );
});
