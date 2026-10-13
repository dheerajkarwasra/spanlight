import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ThemeContext } from "@/lib/theme";

import { ProductPreview } from "./product-preview";

const captured = vi.hoisted(() => ({ value: false }));

vi.mock("./landing-shots", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    shotSources: () => (captured.value ? { src: "/hero.webp", srcSet: "/hero.webp 1203w" } : null),
  };
});

function renderPreview() {
  render(
    <ThemeContext.Provider
      value={{ theme: "light", resolvedTheme: "light", setTheme: () => undefined }}
    >
      <ProductPreview />
    </ThemeContext.Provider>,
  );
}

const CAPTION = /A real screen from the live demo/;

describe("ProductPreview", () => {
  // Finding T7-08: with no screenshot captured the frame is empty, so its caption must not claim
  // a real screen is shown.
  it("drops the caption while the screenshot is missing", () => {
    captured.value = false;
    renderPreview();
    expect(screen.queryByText(CAPTION)).toBeNull();
  });

  it("captions the screenshot once it exists", () => {
    captured.value = true;
    renderPreview();
    expect(screen.getByText(CAPTION)).toBeInTheDocument();
  });
});
