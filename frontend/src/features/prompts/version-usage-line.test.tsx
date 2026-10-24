import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LOWER_BOUND_REASON } from "@/components/cost-value";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { PromptVersionUsage } from "@/lib/api";

import { VersionUsageLine } from "./version-usage-line";

const USAGE: PromptVersionUsage = {
  traces: 12,
  llm_calls: 14,
  cost_usd: "0.041",
  p95_ms: 820,
  has_unpriced: false,
};

function renderLine(usage: PromptVersionUsage | null) {
  return render(
    <TooltipProvider>
      <VersionUsageLine usage={usage} />
    </TooltipProvider>,
  );
}

describe("VersionUsageLine", () => {
  it("shows traces and cost", () => {
    const { container } = renderLine(USAGE);
    expect(container).toHaveTextContent("12 traces · $0.0410");
    expect(screen.queryByRole("img", { name: LOWER_BOUND_REASON })).not.toBeInTheDocument();
  });

  it("uses the singular for one trace", () => {
    const { container } = renderLine({ ...USAGE, traces: 1, llm_calls: 1 });
    expect(container).toHaveTextContent("1 trace · $0.0410");
  });

  it("marks the cost as a lower bound when some calls are unpriced", () => {
    renderLine({ ...USAGE, has_unpriced: true });
    expect(screen.getByRole("img", { name: LOWER_BOUND_REASON })).toBeInTheDocument();
  });

  it("explains an unknown cost instead of showing $0", () => {
    const { container } = renderLine({ ...USAGE, cost_usd: null, has_unpriced: true });
    expect(container).not.toHaveTextContent("$");
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("says so when the version had no calls", () => {
    const { container } = renderLine({
      traces: 0,
      llm_calls: 0,
      cost_usd: null,
      p95_ms: null,
      has_unpriced: false,
    });
    expect(container).toHaveTextContent("No calls in 7 days");
    expect(container).not.toHaveTextContent("$");
  });

  it("shows an explained dash when usage was not loaded", () => {
    renderLine(null);
    expect(screen.getByLabelText("Unknown: Usage not loaded")).toHaveTextContent("—");
  });
});
