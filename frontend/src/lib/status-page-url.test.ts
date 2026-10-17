import { describe, expect, it } from "vitest";

import { statusPageUrl } from "./status-page-url";

describe("statusPageUrl", () => {
  it("links to the three polled status pages", () => {
    for (const url of [
      "https://status.claude.com/incidents/abc",
      "https://status.openai.com/incidents/01J",
      "https://status.cloud.google.com/incidents/xyz",
    ]) {
      expect(statusPageUrl(url)).toBe(url);
    }
  });

  it("rejects user info, other hosts, other schemes and malformed values", () => {
    for (const url of [
      "https://status.x@evil.example/",
      "https://status.openai.com@evil.example/incidents/1",
      "https://user:pw@status.openai.com/incidents/1",
      "https://status.evil.example/incidents/1",
      "https://status.anthropic.com/incidents/1",
      "https://notstatus.openai.com/x",
      "https://evil.example/status.openai.com",
      "https://status.openai.com:8443/incidents/1",
      "http://status.openai.com/incidents/1",
      "javascript:alert(1)",
      "data:text/html,hi",
      "not a url",
      "",
    ]) {
      expect(statusPageUrl(url)).toBeNull();
    }
    expect(statusPageUrl(null)).toBeNull();
  });
});
