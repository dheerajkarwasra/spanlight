import { describe, expect, it } from "vitest";

import { formatPayloadBytes, GUARD_BYTES, guardPayload, PREVIEW_BYTES } from "./payload-guard";

describe("guardPayload", () => {
  it("leaves a payload up to 64 KB whole", () => {
    const text = "a".repeat(GUARD_BYTES);
    expect(guardPayload(text)).toEqual({ truncated: false, shown: text, totalBytes: GUARD_BYTES });
  });

  it("shows the first 16 KB of a larger payload", () => {
    const text = "a".repeat(GUARD_BYTES + 1);
    const guarded = guardPayload(text);
    expect(guarded.truncated).toBe(true);
    expect(guarded.shown).toBe("a".repeat(PREVIEW_BYTES));
    expect(guarded.totalBytes).toBe(GUARD_BYTES + 1);
  });

  it("counts UTF-8 bytes and never splits a character", () => {
    // "é" is 2 bytes: 40,000 of them are 80,000 bytes, and 16,384 bytes hold exactly 8,192.
    const guarded = guardPayload(`x${"é".repeat(40_000)}`);
    expect(guarded.totalBytes).toBe(80_001);
    expect(guarded.shown).toBe(`x${"é".repeat(8191)}`);
  });
});

describe("formatPayloadBytes", () => {
  it.each([
    [PREVIEW_BYTES, "16 KB"],
    [1.2 * 1024 * 1024, "1.2 MB"],
    [500, "1 KB"],
  ])("formats %d bytes as %s", (bytes, label) => {
    expect(formatPayloadBytes(bytes)).toBe(label);
  });
});
