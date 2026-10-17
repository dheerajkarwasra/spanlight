import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { Kpis } from "@/lib/api";

import { KpiCards } from "./kpi-cards";

const KPIS: Kpis = {
  traces: 3,
  llm_calls: 4,
  error_rate: 0.5,
  p50_ms: 1200,
  p95_ms: 1200,
  cost_usd: "0.000662",
  unpriced_calls: 0,
  input_tokens: 24,
  output_tokens: 80,
};

describe("KpiCards", () => {
  it("fits every value to its tile instead of cutting it with an ellipsis", () => {
    const { container } = render(
      <TooltipProvider>
        <KpiCards
          metrics={{ current: KPIS, previous: KPIS, approximate: false }}
          points={null}
          comparisonLabel="vs previous 24h"
        />
      </TooltipProvider>,
    );
    const values = [...container.querySelectorAll<HTMLElement>("[data-kpi-value]")];
    expect(values).toHaveLength(4);
    for (const value of values) {
      expect(value).not.toHaveClass("truncate");
      expect(value.style.fontSize).toContain("100cqi");
    }
    expect(screen.getByText(/50\.0/)).toBeInTheDocument();
  });
});
