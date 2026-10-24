import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AttributionEmpty } from "./attribution-states";

describe("AttributionEmpty", () => {
  it("says the window's traces are not counted yet when the rollup has none but traces wait", () => {
    render(<AttributionEmpty groupBy="trace_name" metadataKey={null} pendingTraces={432} />);
    expect(screen.getByText("Not counted yet")).toBeInTheDocument();
    expect(
      screen.getByText("432 traces in this window haven't been added to the daily rollup yet."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No traces in this window")).not.toBeInTheDocument();
  });

  it("keeps the empty state when nothing is pending", () => {
    render(<AttributionEmpty groupBy="trace_name" metadataKey={null} pendingTraces={0} />);
    expect(screen.getByText("No traces in this window")).toBeInTheDocument();
  });
});
