import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { EvidenceCard } from "./evidence-card";

describe("EvidenceCard", () => {
  it("shows an error spike with no baseline yet as unknown, never as 0", () => {
    render(
      <TooltipProvider>
        <EvidenceCard
          insight={{
            kind: "error_spike",
            evidence: {
              trace_ids: [],
              metrics: { error_rate: "0.3000", baseline_error_rate: null, calls: 50, errors: 15 },
              window: { start: "2026-11-01T12:00:00Z", end: "2026-11-02T12:00:00Z" },
            },
          }}
        />
      </TooltipProvider>,
    );

    // No chart without a baseline: the observed rate stays a tile.
    const metrics = screen.getByRole("list", { name: "Metrics" });
    expect(within(metrics).getByText(/^30(\.0)?%$/)).toBeInTheDocument();
    const baseline = within(metrics).getByText("Baseline error rate").closest("li");
    expect(baseline).not.toBeNull();
    expect(within(baseline as HTMLElement).getByLabelText(/^Unknown:/)).toHaveTextContent("—");
    expect(baseline).not.toHaveTextContent(/None|%/);
  });
});
