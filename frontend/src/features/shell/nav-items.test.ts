import { describe, expect, it } from "vitest";

import { NAV_ITEMS } from "./nav-items";

describe("NAV_ITEMS", () => {
  it("gives every page its own shortcut", () => {
    const shortcuts = NAV_ITEMS.map((item) => item.shortcut);
    expect(new Set(shortcuts).size).toBe(shortcuts.length);
  });

  it("lists Attribution after Users with the shortcut G C", () => {
    const labels = NAV_ITEMS.map((item) => item.label);
    expect(labels.indexOf("Attribution")).toBe(labels.indexOf("Users") + 1);
    expect(NAV_ITEMS.find((item) => item.label === "Attribution")).toMatchObject({
      to: "/$orgId/$projectId/attribution",
      shortcut: "G C",
    });
  });
});
