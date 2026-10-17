import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChartViewToggle } from "./chart-view-toggle";

describe("ChartViewToggle", () => {
  it("names the view a press shows, without a pressed state", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<ChartViewToggle view="chart" onChange={onChange} />);
    const button = screen.getByRole("button", { name: "Show table" });
    expect(button).not.toHaveAttribute("aria-pressed");
    expect(button).toHaveClass("max-md:min-h-11");
    await userEvent.click(button);
    expect(onChange).toHaveBeenCalledWith("table");

    rerender(<ChartViewToggle view="table" onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Show chart" }));
    expect(onChange).toHaveBeenLastCalledWith("chart");
  });
});
