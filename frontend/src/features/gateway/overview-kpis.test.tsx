import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { GatewayOverview } from "@/lib/api";
import { metricEmWidth } from "@/lib/metric-fit";

import { buildGatewayKpis, windowPhrase } from "./overview-format";
import { OverviewKpis } from "./overview-kpis";

/** One request in the last 24 h, and it failed: the state that clipped "100.0%" at 375 px. */
function failingOverview(): GatewayOverview {
  return {
    requests: 1,
    errors: 1,
    error_rate: 1,
    cache: { hits: 0, misses: 0, hit_rate: null },
    fallbacks: 0,
    retries: 0,
    p95_overhead_ms: 122,
    p95_ttft_ms: null,
    by_key: [{ key_id: "key_1" }],
    faults: [{ scenario: "provider_5xx", count: 1 }],
  } as unknown as GatewayOverview;
}

describe("OverviewKpis", () => {
  it("shows every value in full, at one size fitted to the widest value", () => {
    render(
      <TooltipProvider>
        <OverviewKpis kpis={buildGatewayKpis(failingOverview(), "24h")} />
      </TooltipProvider>,
    );

    const tiles = within(screen.getByRole("list", { name: "Gateway metrics" })).getAllByRole(
      "listitem",
    );
    const errorTile = tiles.at(1);
    if (!errorTile) {
      throw new Error("no error rate tile");
    }
    const errorRate = within(errorTile).getByText("100.0%");
    expect(errorRate).not.toHaveClass("truncate");
    expect(errorRate.closest("[data-kpi-value]")).toHaveStyle({
      fontSize: `min(var(--text-metric), calc(100cqi / ${metricEmWidth("100.0%").toFixed(2)}))`,
    });

    const sizes = tiles.map(
      (tile) => tile.querySelector<HTMLElement>("[data-kpi-value]")?.style.fontSize,
    );
    expect(new Set(sizes).size).toBe(1);
  });
});

describe("windowPhrase", () => {
  it("keeps the number and its unit on one line", () => {
    expect(windowPhrase("24h")).toBe("last 24 h");
    expect(windowPhrase("7d")).toBe("last 7 d");
  });
});
