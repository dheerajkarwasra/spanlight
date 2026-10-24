import { describe, expect, it } from "vitest";

import { freshnessLabel } from "./freshness";

// Local times: the label reads the viewer's clock.
const NOW = new Date(2026, 9, 21, 14, 30);
const AT_1405 = new Date(2026, 9, 21, 14, 5).toISOString();

describe("freshnessLabel", () => {
  it("names the recompute time and the traces that arrived since", () => {
    expect(freshnessLabel(AT_1405, 37, NOW)).toBe(
      "Updated 14:05 · 37 traces since then aren't included yet",
    );
  });

  it("says nothing is pending when every trace is included", () => {
    expect(freshnessLabel(AT_1405, 0, NOW)).toBe("Updated 14:05");
    expect(freshnessLabel(AT_1405, null, NOW)).toBe("Updated 14:05");
  });

  it("uses the singular for one trace", () => {
    expect(freshnessLabel(AT_1405, 1, NOW)).toBe(
      "Updated 14:05 · 1 trace since then isn't included yet",
    );
  });

  it("shows the server's cap as a lower bound", () => {
    expect(freshnessLabel(AT_1405, 10_000, NOW)).toBe(
      "Updated 14:05 · 10,000+ traces since then aren't included yet",
    );
  });

  it("names the day of a recompute before today, lowercase mid-sentence", () => {
    expect(freshnessLabel(new Date(2026, 9, 20, 23, 50).toISOString(), null, NOW)).toBe(
      "Updated yesterday 23:50",
    );
    expect(freshnessLabel(new Date(2026, 9, 18, 9, 0).toISOString(), null, NOW)).toBe(
      "Updated Oct 18 09:00",
    );
  });

  it("says the window was never computed, never 'Updated' with no time", () => {
    expect(freshnessLabel(null, 432, NOW)).toBe(
      "Not computed yet · 432 traces in this window aren't included yet",
    );
    expect(freshnessLabel(null, null, NOW)).toBe("Not computed yet");
    expect(freshnessLabel("not-a-date", null, NOW)).toBe("Not computed yet");
  });
});
