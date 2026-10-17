import { describe, expect, it } from "vitest";

import { horizontalReveal } from "./reveal-in-scroller";

const BOX = { left: 0, right: 343 };

describe("horizontalReveal", () => {
  it("does not move for an item that already shows", () => {
    expect(horizontalReveal(BOX, { left: 40, right: 120 }, 16)).toBe(0);
  });

  it("scrolls right to bring an off-screen last tab in, past the gutter", () => {
    expect(horizontalReveal(BOX, { left: 380, right: 470 }, 16)).toBe(143);
  });

  it("scrolls left for an item cut at the start", () => {
    expect(horizontalReveal(BOX, { left: -50, right: 30 }, 16)).toBe(-66);
  });

  it("keeps the start of an item wider than the box in view", () => {
    expect(horizontalReveal(BOX, { left: 100, right: 600 }, 16)).toBe(84);
  });
});
