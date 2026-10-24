import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { COMPUTED_AT_EXPLANATION, NOT_COMPUTED_EXPLANATION } from "./computed-at";
import { ComputedAtChip } from "./computed-at-chip";

const AT_1405 = new Date(new Date().setHours(14, 5, 0, 0)).toISOString();
const UPDATED = "Updated 14:05 · 37 traces since then aren't included yet";

function renderChip(value: string | null, pendingTraces: number | null = 37) {
  render(
    <TooltipProvider>
      <ComputedAtChip value={value} pendingTraces={pendingTraces} />
    </TooltipProvider>,
  );
}

function tap(element: HTMLElement) {
  fireEvent.pointerDown(element, { pointerType: "touch" });
  fireEvent.click(element);
}

describe("ComputedAtChip", () => {
  it("shows the freshness line and names the button after it", () => {
    renderChip(AT_1405);
    expect(screen.getByText(UPDATED)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: UPDATED })).toBeInTheDocument();
  });

  it("says 'Not computed yet' with the pending traces when the value is null", () => {
    renderChip(null, 432);
    const label = "Not computed yet · 432 traces in this window aren't included yet";
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  });

  it("toggles the explanation on a touch tap", () => {
    renderChip(AT_1405);
    const button = screen.getByRole("button", { name: UPDATED });

    tap(button);
    expect(screen.getByRole("tooltip")).toHaveTextContent(COMPUTED_AT_EXPLANATION);
    expect(button).toHaveAccessibleDescription(COMPUTED_AT_EXPLANATION);

    tap(button);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("explains the missing value on a touch tap when nothing is computed yet", () => {
    renderChip(null, null);
    tap(screen.getByRole("button", { name: "Not computed yet" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent(NOT_COMPUTED_EXPLANATION);
  });

  it("does not toggle on a mouse click", () => {
    renderChip(AT_1405);
    const button = screen.getByRole("button", { name: UPDATED });
    fireEvent.pointerDown(button, { pointerType: "mouse" });
    fireEvent.click(button);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
