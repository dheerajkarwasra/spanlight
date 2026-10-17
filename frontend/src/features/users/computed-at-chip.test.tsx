import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { COMPUTED_AT_EXPLANATION, NOT_COMPUTED_EXPLANATION } from "./computed-at";
import { ComputedAtChip } from "./computed-at-chip";

function renderChip(value: string | null) {
  render(
    <TooltipProvider>
      <ComputedAtChip value={value} />
    </TooltipProvider>,
  );
}

function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

function tap(element: HTMLElement) {
  fireEvent.pointerDown(element, { pointerType: "touch" });
  fireEvent.click(element);
}

describe("ComputedAtChip", () => {
  it("names the button after the visible label", () => {
    renderChip(minutesAgo(4));
    expect(screen.getByRole("button", { name: "Updated 4 min ago" })).toBeInTheDocument();
  });

  it("reads 'Updated —' and names the button 'not computed yet' when the value is null", () => {
    renderChip(null);
    expect(screen.getByRole("button", { name: "Updated: not computed yet" })).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("toggles the explanation on a touch tap", () => {
    renderChip(minutesAgo(4));
    const button = screen.getByRole("button", { name: "Updated 4 min ago" });

    tap(button);
    expect(screen.getByRole("tooltip")).toHaveTextContent(COMPUTED_AT_EXPLANATION);
    expect(button).toHaveAccessibleDescription(COMPUTED_AT_EXPLANATION);

    tap(button);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("explains the missing value on a touch tap when nothing is computed yet", () => {
    renderChip(null);
    tap(screen.getByRole("button", { name: "Updated: not computed yet" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent(NOT_COMPUTED_EXPLANATION);
  });

  it("does not toggle on a mouse click", () => {
    renderChip(minutesAgo(4));
    const button = screen.getByRole("button", { name: "Updated 4 min ago" });
    fireEvent.pointerDown(button, { pointerType: "mouse" });
    fireEvent.click(button);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
