import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Logo, LogoMark } from "./logo";

function rectGeometry(rect: SVGRectElement) {
  return ["x", "y", "width", "height", "rx"].map((name) => rect.getAttribute(name)).join(" ");
}

describe("Logo", () => {
  it("is named Spanlight exactly once", () => {
    render(
      <a href="/">
        <Logo />
      </a>,
    );
    expect(screen.getByRole("link")).toHaveAccessibleName("Spanlight");
    expect(screen.getByRole("img", { name: "Spanlight" })).toBeInTheDocument();
  });

  it("draws a single-line lowercase wordmark that assistive tech skips", () => {
    render(<Logo />);
    const letters = screen.getByText("spanlight");
    expect(letters.closest('[aria-hidden="true"]')).not.toBeNull();
    expect(letters).toHaveClass("font-brand", "leading-none");
  });

  it("can show the mark alone and keeps its name", () => {
    render(<Logo withWordmark={false} />);
    expect(screen.getByRole("img", { name: "Spanlight" }).querySelector("svg")).not.toBeNull();
    expect(screen.queryByText("spanlight")).toBeNull();
  });
});

describe("LogoMark", () => {
  it("draws four staggered spans on a 64-unit grid", () => {
    const { container } = render(<LogoMark />);
    expect(container.querySelector("svg")).toHaveAttribute("viewBox", "0 0 64 64");
    expect([...container.querySelectorAll("rect")].map(rectGeometry)).toEqual([
      "4 10 30 8 4",
      "13 22 30 8 4",
      "22 34 38 8 4",
      "31 46 22 8 4",
    ]);
  });

  it("lights the third span lime on dark surfaces and dims the rest", () => {
    const { container } = render(<LogoMark tone="on-dark" />);
    const rects = [...container.querySelectorAll("rect")];
    expect(rects.map((rect) => rect.getAttribute("fill"))).toEqual([
      "currentColor",
      "currentColor",
      "var(--lime)",
      "currentColor",
    ]);
    expect(rects.map((rect) => rect.getAttribute("fill-opacity"))).toEqual([
      "0.3",
      "0.3",
      "1",
      "0.3",
    ]);
  });

  it("draws the lit span in the text colour on light surfaces", () => {
    const { container } = render(<LogoMark />);
    const rects = [...container.querySelectorAll("rect")];
    expect(rects.every((rect) => rect.getAttribute("fill") === "currentColor")).toBe(true);
    expect(rects.map((rect) => rect.getAttribute("fill-opacity"))).toEqual([
      "0.2",
      "0.2",
      "1",
      "0.2",
    ]);
  });

  it("grows to 40px for the mobile app bar", () => {
    const { container } = render(<LogoMark size="lg" />);
    expect(container.firstElementChild).toHaveClass("size-10");
  });
});
