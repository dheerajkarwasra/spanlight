import { describe, expect, it } from "vitest";

import { ERROR_CLASSES, errorClassColor, errorClassLabel, isErrorClass } from "./error-class";

describe("ERROR_CLASSES", () => {
  it("is the one display order of all ten classes, unknown last", () => {
    expect(ERROR_CLASSES).toEqual([
      "rate_limit",
      "provider_5xx",
      "timeout",
      "network",
      "auth",
      "context_length",
      "content_filter",
      "client",
      "gateway",
      "unknown",
    ]);
  });
});

describe("errorClassColor", () => {
  it("names the class's chart token", () => {
    expect(errorClassColor("rate_limit")).toBe("var(--chart-class-rate-limit)");
    expect(errorClassColor("provider_5xx")).toBe("var(--chart-class-provider-5xx)");
    expect(errorClassColor("unknown")).toBe("var(--chart-class-unknown)");
  });
});

describe("isErrorClass and errorClassLabel", () => {
  it("accepts only known classes and names them in plain words", () => {
    expect(isErrorClass("gateway")).toBe(true);
    expect(isErrorClass("teapot")).toBe(false);
    expect(errorClassLabel("rate_limit")).toBe("Rate limited");
  });
});
