import { describe, expect, it } from "vitest";

import { rootStatusLabel } from "./root-status";

describe("rootStatusLabel", () => {
  it("names a missing root and an unknown one, and nothing once the root arrived", () => {
    expect(rootStatusLabel(false)).toBe("Root span not received");
    expect(rootStatusLabel(null)).toBe("Root status unavailable");
    expect(rootStatusLabel(true)).toBeNull();
  });
});
