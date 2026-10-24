import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { AttributionResponse } from "@/lib/api";
import { resolveRange } from "@/lib/time-range";

import { response } from "./attribution-test-data";
import { WindowNote } from "./window-note";

const AT_1405 = new Date(new Date().setHours(14, 5, 0, 0)).toISOString();

function renderNote(page: AttributionResponse | undefined, range: "24h" | "30d" = "30d") {
  render(
    <TooltipProvider>
      <WindowNote range={resolveRange({ range })} page={page} />
    </TooltipProvider>,
  );
}

describe("WindowNote", () => {
  it("says when the rollup was computed and how many traces it doesn't include yet", () => {
    renderNote(response({ computed_at: AT_1405, pending_traces: 37 }));
    expect(
      screen.getByText("Updated 14:05 · 37 traces since then aren't included yet"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Updated every 15 min")).not.toBeInTheDocument();
  });

  it("says the rollup was never computed for the window, not 'Updated' with no time", () => {
    renderNote(response({ computed_at: null, pending_traces: 432 }));
    expect(
      screen.getByText("Not computed yet · 432 traces in this window aren't included yet"),
    ).toBeInTheDocument();
  });

  it("keeps the Live badge on the raw source, with no freshness line", () => {
    renderNote(response({ source: "raw", computed_at: null, pending_traces: null }), "24h");
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.queryByText(/computed|Updated/)).not.toBeInTheDocument();
  });
});
