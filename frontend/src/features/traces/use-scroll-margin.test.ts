import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useScrollMargin } from "./use-scroll-margin";

function box(height: number, sticky: boolean): HTMLElement {
  const element = document.createElement("div");
  if (sticky) {
    element.style.position = "sticky";
    element.style.top = "0px";
    element.className = "sticky";
  }
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue(DOMRect.fromRect({ height }));
  return element;
}

describe("useScrollMargin in a scroll box", () => {
  it("pads scrolled-to rows below the sticky bar above the list, not one beside it", () => {
    const scroller = document.createElement("div");
    const bar = box(56, true);
    const list = box(0, false);
    const inspector = box(600, true);
    scroller.append(bar, list, inspector);
    document.body.append(scroller);

    const { result } = renderHook(() => useScrollMargin({ current: list }, true, scroller));

    expect(result.current.scrollPaddingStart).toBe(56);
    scroller.remove();
  });
});

describe("useScrollMargin on the page", () => {
  it("counts the trace's own sticky bar, not a tall sticky rail beside the content", () => {
    const rail = box(900, true);
    const bar = box(56, true);
    bar.style.top = "64px";
    const list = box(0, false);
    document.body.append(rail, bar, list);

    const { result } = renderHook(() =>
      useScrollMargin({ current: list }, true, undefined, { current: bar }),
    );

    expect(result.current.scrollPaddingStart).toBe(120);
    rail.remove();
    bar.remove();
    list.remove();
  });
});
