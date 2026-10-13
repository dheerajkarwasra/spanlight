import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { revealScrollTop, scrollCues } from "./rail-scroll";
import { RailScrollArea } from "./rail-scroll-area";

describe("scrollCues", () => {
  it("shows no cue when everything fits", () => {
    expect(scrollCues({ scrollTop: 0, scrollHeight: 400, clientHeight: 400 })).toEqual({
      above: false,
      below: false,
    });
  });

  it("points down at the top of an overflowing list and up at its end", () => {
    expect(scrollCues({ scrollTop: 0, scrollHeight: 800, clientHeight: 500 })).toEqual({
      above: false,
      below: true,
    });
    expect(scrollCues({ scrollTop: 150, scrollHeight: 800, clientHeight: 500 })).toEqual({
      above: true,
      below: true,
    });
    expect(scrollCues({ scrollTop: 300, scrollHeight: 800, clientHeight: 500 })).toEqual({
      above: true,
      below: false,
    });
  });

  it("treats a sub-pixel remainder as the end, as fractional zoom leaves one", () => {
    expect(scrollCues({ scrollTop: 299.5, scrollHeight: 800, clientHeight: 500 }).below).toBe(
      false,
    );
  });
});

describe("revealScrollTop", () => {
  const view = { scrollTop: 0, clientHeight: 500 };

  it("leaves an item that is already clear of the cues where it is", () => {
    expect(revealScrollTop(view, { top: 100, bottom: 144 }, 32)).toBe(0);
  });

  it("scrolls just far enough to lift an item below the fold clear of the bottom cue", () => {
    // Budgets at 708..752 in an 800 px list: the rail at 1440x900 (finding T7-09).
    expect(revealScrollTop(view, { top: 708, bottom: 752 }, 32)).toBe(284);
  });

  it("scrolls back up to an item above the view", () => {
    expect(
      revealScrollTop({ scrollTop: 300, clientHeight: 500 }, { top: 48, bottom: 92 }, 32),
    ).toBe(16);
  });

  it("never scrolls above the top", () => {
    expect(revealScrollTop({ scrollTop: 300, clientHeight: 500 }, { top: 0, bottom: 44 }, 32)).toBe(
      0,
    );
  });
});

function scroller(container: HTMLElement): HTMLElement {
  const element = container.querySelector<HTMLElement>("[data-rail-scroller]");
  if (!element) {
    throw new Error("scroller not rendered");
  }
  return element;
}

function cue(container: HTMLElement, side: "above" | "below"): HTMLElement | null {
  return container.querySelector<HTMLElement>(`[data-scroll-cue="${side}"]`);
}

describe("RailScrollArea", () => {
  it("shows a cue on each side that has more pages to scroll to", () => {
    const { container } = render(
      <RailScrollArea>
        <a href="/a">A</a>
      </RailScrollArea>,
    );
    const element = scroller(container);
    Object.defineProperty(element, "scrollHeight", { configurable: true, value: 800 });
    Object.defineProperty(element, "clientHeight", { configurable: true, value: 500 });

    fireEvent.scroll(element);
    expect(cue(container, "above")).toHaveAttribute("data-visible", "false");
    expect(cue(container, "below")).toHaveAttribute("data-visible", "true");

    element.scrollTop = 300;
    fireEvent.scroll(element);
    expect(cue(container, "above")).toHaveAttribute("data-visible", "true");
    expect(cue(container, "below")).toHaveAttribute("data-visible", "false");
  });

  it("keeps the cues out of the accessibility tree", () => {
    const { container } = render(
      <RailScrollArea>
        <a href="/a">A</a>
      </RailScrollArea>,
    );
    expect(cue(container, "above")).toHaveAttribute("aria-hidden", "true");
    expect(cue(container, "below")).toHaveAttribute("aria-hidden", "true");
  });
});
