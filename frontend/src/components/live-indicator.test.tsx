import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { LiveIndicator } from "./live-indicator";

describe("LiveIndicator", () => {
  it("keeps one status region from the first connection, so the first state is announced", () => {
    const { rerender } = render(
      <TooltipProvider>
        <LiveIndicator status="connecting" />
      </TooltipProvider>,
    );
    const region = screen.getByRole("status");
    expect(region).toBeEmptyDOMElement();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    rerender(
      <TooltipProvider>
        <LiveIndicator status="live" />
      </TooltipProvider>,
    );

    expect(screen.getByRole("status")).toBe(region);
    expect(region).toHaveTextContent("Live updates on");
  });
});
