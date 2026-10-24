import { fireEvent, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useSyncedScroll } from "./use-synced-scroll";

/** jsdom has no layout: give a box a scroll range of `range` px. */
function box(range: number): HTMLDivElement {
  const element = document.createElement("div");
  Object.defineProperty(element, "clientHeight", { value: 100 });
  Object.defineProperty(element, "scrollHeight", { value: 100 + range });
  return element;
}

describe("useSyncedScroll", () => {
  it("moves the other box to the same ratio and ignores the echo", () => {
    const a = box(100);
    const b = box(200);
    renderHook(() => {
      useSyncedScroll({ current: a }, { current: b });
    });

    a.scrollTop = 50;
    fireEvent.scroll(a);
    expect(b.scrollTop).toBe(100);

    // B's own scroll event for that move must not move A back.
    a.scrollTop = 80;
    fireEvent.scroll(b);
    expect(a.scrollTop).toBe(80);

    // A real scroll of B moves A.
    b.scrollTop = 150;
    fireEvent.scroll(b);
    expect(a.scrollTop).toBe(75);
  });

  it("leaves a box without a scroll range alone", () => {
    const a = box(0);
    const b = box(200);
    renderHook(() => {
      useSyncedScroll({ current: a }, { current: b });
    });
    fireEvent.scroll(a);
    expect(b.scrollTop).toBe(0);
  });
});
