import { describe, expect, it } from "vitest";

import { keyTargetOf, shortcutFor, type KeyInput, type KeyTarget } from "./shortcuts";

function press(key: string, target: KeyTarget = "page", helpOpen = false, modifiers = {}) {
  const input: KeyInput = { key, metaKey: false, ctrlKey: false, altKey: false, ...modifiers };
  return shortcutFor(input, target, helpOpen);
}

describe("shortcutFor", () => {
  it("picks options 1 to 9, never 0", () => {
    expect(press("1")).toEqual({ type: "pick", index: 0 });
    expect(press("9")).toEqual({ type: "pick", index: 8 });
    expect(press("0")).toBeNull();
  });

  it("lets digits type into a focused number score", () => {
    expect(press("3", "numeric")).toBeNull();
    expect(press("Enter", "numeric")).toEqual({ type: "submit" });
    expect(press("s", "numeric")).toEqual({ type: "skip" });
  });

  it("never fires while typing a comment, even Enter or ?", () => {
    for (const key of ["1", "s", "n", "Enter", "?", "Escape"]) {
      expect(press(key, "text")).toBeNull();
    }
  });

  it("leaves browser and OS shortcuts alone", () => {
    expect(press("s", "page", false, { metaKey: true })).toBeNull();
    expect(press("n", "page", false, { ctrlKey: true })).toBeNull();
    expect(press("1", "page", false, { altKey: true })).toBeNull();
  });

  it("keeps Enter's native meaning on a focused button or link", () => {
    expect(press("Enter", "control")).toBeNull();
    expect(press("Enter", "page")).toEqual({ type: "submit" });
  });

  it("reads S and N in either case", () => {
    expect(press("S")).toEqual({ type: "skip" });
    expect(press("N")).toEqual({ type: "next" });
  });

  it("only toggles the help while it is open, so a key can't submit behind it", () => {
    expect(press("?", "page", true)).toEqual({ type: "toggle_help" });
    expect(press("Enter", "page", true)).toBeNull();
    expect(press("1", "page", true)).toBeNull();
    expect(press("Escape", "page", true)).toBeNull();
    expect(press("Escape", "page", false)).toEqual({ type: "escape" });
  });
});

describe("keyTargetOf", () => {
  function element<K extends keyof HTMLElementTagNameMap>(
    tag: K,
    data: Record<string, string> = {},
  ): HTMLElementTagNameMap[K] {
    const node = document.createElement(tag);
    Object.assign(node.dataset, data);
    return node;
  }

  it("classifies free-text fields as text", () => {
    expect(keyTargetOf(element("textarea"))).toBe("text");
    expect(keyTargetOf(element("input"))).toBe("text");
    expect(keyTargetOf(element("select"))).toBe("text");
  });

  it("classifies a number score input as numeric, not text", () => {
    expect(keyTargetOf(element("input", { scoreInput: "numeric" }))).toBe("numeric");
  });

  it("treats score option buttons as the page, so Enter still submits after picking", () => {
    expect(keyTargetOf(element("button", { scoreOption: "" }))).toBe("page");
    expect(keyTargetOf(element("button"))).toBe("control");
    expect(keyTargetOf(element("a"))).toBe("control");
  });

  it("treats no focus as the page", () => {
    expect(keyTargetOf(null)).toBe("page");
    expect(keyTargetOf(window)).toBe("page");
  });
});
