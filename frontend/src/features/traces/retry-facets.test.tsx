import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RetryFacets } from "./retry-facets";

describe("RetryFacets", () => {
  it("names the sort chip with its current value", () => {
    const { rerender } = render(<RetryFacets search={{}} onChange={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Sort: Newest" })).toBeInTheDocument();

    rerender(<RetryFacets search={{ sort: "retry_cost_usd" }} onChange={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Sort: Retry cost ↓" })).toBeInTheDocument();
  });
});
