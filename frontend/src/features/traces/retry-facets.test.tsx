import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RetryFacets } from "./retry-facets";
import { sortPatch } from "./search";

describe("RetryFacets", () => {
  it("names the sort chip with its current value", () => {
    const { rerender } = render(<RetryFacets search={{}} onChange={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Sort: Newest" })).toBeInTheDocument();

    rerender(<RetryFacets search={{ sort: "retry_cost_usd" }} onChange={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Sort: Retry cost ↓" })).toBeInTheDocument();

    // A header click on a wider screen can leave an ascending sort in the URL.
    rerender(<RetryFacets search={{ sort: "cost", order: "asc" }} onChange={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Sort: Cost ↑" })).toBeInTheDocument();
  });

  it("reaches every sort in both directions, and newest first clears the URL", () => {
    expect(sortPatch("cost:desc")).toEqual({ sort: "cost", order: undefined });
    expect(sortPatch("cost:asc")).toEqual({ sort: "cost", order: "asc" });
    expect(sortPatch("started_at:asc")).toEqual({ sort: "started_at", order: "asc" });
    expect(sortPatch("started_at:desc")).toEqual({ sort: undefined, order: undefined });
  });
});
