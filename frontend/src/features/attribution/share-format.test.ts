import { describe, expect, it } from "vitest";

import { formatShare, shareBarWidth } from "./share-format";

describe("formatShare", () => {
  it("shows a share as a percentage with one decimal", () => {
    expect(formatShare(0.612)).toBe("61.2%");
    expect(formatShare(1)).toBe("100.0%");
    expect(formatShare(0)).toBe("0.0%");
  });

  it("shows a share below 0.1 % as <0.1%, not 0.0%", () => {
    expect(formatShare(0.0004)).toBe("<0.1%");
  });

  it("shows an unknown share as a dash, never 0", () => {
    expect(formatShare(null)).toBe("—");
  });
});

describe("shareBarWidth", () => {
  it("is the share as a CSS percentage", () => {
    expect(shareBarWidth(0.612)).toBe("61.2%");
    expect(shareBarWidth(0)).toBe("0%");
  });

  it("keeps a tiny share visible at 1 px", () => {
    expect(shareBarWidth(0.0004)).toBe("1px");
  });

  it("caps the bar at the track", () => {
    expect(shareBarWidth(1.2)).toBe("100%");
  });

  it("draws no bar for an unknown share", () => {
    expect(shareBarWidth(null)).toBeNull();
  });
});
