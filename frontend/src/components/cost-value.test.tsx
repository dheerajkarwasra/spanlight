import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { CostValue, UNKNOWN_TOTAL_REASON } from "./cost-value";

describe("CostValue on a total", () => {
  it("says a lower bound comes from spans with no cost, priced or not", () => {
    render(
      <TooltipProvider>
        <CostValue cost="0.0164" hasUnpriced />
      </TooltipProvider>,
    );
    // A cut stream has a price and still no cost: "no price" would be the wrong reason.
    expect(
      screen.getByRole("img", {
        name: "Some spans have no known cost (no price, or usage not reported); total is a lower bound",
      }),
    ).toBeInTheDocument();
  });

  it("explains an unknown total without blaming the price alone", () => {
    render(
      <TooltipProvider>
        <CostValue cost={null} unknownReason={UNKNOWN_TOTAL_REASON} />
      </TooltipProvider>,
    );
    expect(
      screen.getByLabelText("Unknown: No span has a known cost (no price, or usage not reported)"),
    ).toHaveTextContent("—");
  });
});
