import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { GatewayOverview } from "@/lib/api";

import { buildGatewayKpis, windowPhrase } from "./overview-format";
import { OverviewKpis } from "./overview-kpis";

function overview(patch: Partial<GatewayOverview> = {}): GatewayOverview {
  return {
    requests: 200,
    errors: 4,
    error_rate: 0.02,
    cache: { hits: 3932, misses: 14272, hit_rate: 0.216 },
    fallbacks: 41,
    retries: 7,
    rate_limited: 12,
    p95_overhead_ms: 38,
    p95_upstream_ms: 2100,
    p95_ttft_ms: 640,
    previous: {
      requests: 160,
      errors: 2,
      error_rate: 0.0125,
      p95_overhead_ms: 42,
      cache_hit_rate: 0.193,
      fallbacks: 32,
      rate_limited: 3,
    },
    series: { bucket: "hour", points: [] },
    by_target: [],
    by_key: [],
    faults: [],
    ...patch,
  };
}

function kpi(id: string, data = overview()) {
  const found = buildGatewayKpis(data, "24h").find((card) => card.id === id);
  if (!found) {
    throw new Error(`no ${id} card`);
  }
  return found;
}

describe("buildGatewayKpis", () => {
  it("builds the four cards in the frame's order", () => {
    expect(buildGatewayKpis(overview(), "24h").map((card) => [card.label, card.value])).toEqual([
      ["Requests", "200"],
      ["Latency", "38 ms"],
      ["Cache hit rate", "21.6%"],
      ["Fallbacks", "41"],
    ]);
  });

  it("puts the supporting numbers in the lines under each value", () => {
    expect(kpi("requests").lines).toEqual(["98.0% success · 4 errors"]);
    expect(kpi("latency").lines).toEqual([
      { prefix: "Upstream p95", value: "2.10 s", reason: "No gateway overhead recorded." },
      { prefix: "TTFT p95", value: "640 ms", reason: "No streaming requests in this window." },
    ]);
    expect(kpi("cache_hit_rate").lines).toEqual(["3,932 hits · 14,272 misses"]);
    // The value is fallbacks only; rate limits and retries are the line under it.
    expect(kpi("fallbacks").lines).toEqual(["12 rate-limited · 7 retries"]);
  });

  it("compares each card with the previous window, in the direction that is good news", () => {
    const requests = kpi("requests").delta;
    expect(requests).toMatchObject({
      delta: 25,
      increaseIsGood: null,
      comparisonLabel: "vs previous 24 h",
    });

    const latency = kpi("latency").delta;
    expect(latency).toMatchObject({ delta: -4, increaseIsGood: false });
    expect(latency?.format?.(4)).toBe("4 ms");

    const cache = kpi("cache_hit_rate").delta;
    expect(cache).toMatchObject({ delta: 2.3, increaseIsGood: true });
    expect(cache?.format?.(2.3)).toBe("2.3 pt");

    const fallbacks = kpi("fallbacks").delta;
    expect(fallbacks).toMatchObject({ delta: 9, increaseIsGood: false });
    expect(fallbacks?.format?.(9)).toBe("9");
  });

  it("shows no pill when the previous window has nothing to compare with", () => {
    const data = overview({
      previous: {
        requests: 0,
        errors: 0,
        error_rate: null,
        p95_overhead_ms: null,
        cache_hit_rate: null,
        fallbacks: 0,
        rate_limited: 0,
      },
    });
    expect(buildGatewayKpis(data, "24h").map((card) => card.delta)).toEqual([
      undefined,
      undefined,
      undefined,
      undefined,
    ]);
  });

  it("drops a pill whose previous value is unknown and keeps the others", () => {
    const data = overview({
      previous: { ...overview().previous, p95_overhead_ms: null, cache_hit_rate: null },
    });
    expect(kpi("latency", data).delta).toBeUndefined();
    expect(kpi("cache_hit_rate", data).delta).toBeUndefined();
    expect(kpi("requests", data).delta).toBeDefined();
  });

  it("names the previous window by the selected range", () => {
    expect(buildGatewayKpis(overview(), "1h")[0]?.delta?.comparisonLabel).toBe("vs previous 1 h");
    expect(buildGatewayKpis(overview(), "7d")[0]?.delta?.comparisonLabel).toBe("vs previous 7 d");
  });
});

describe("OverviewKpis", () => {
  it("renders four cards, an unknown upstream time as a dash with its reason", () => {
    render(
      <TooltipProvider>
        <OverviewKpis kpis={buildGatewayKpis(overview({ p95_upstream_ms: null }), "24h")} />
      </TooltipProvider>,
    );

    const list = screen.getByRole("list", { name: "Gateway metrics" });
    expect(list).toHaveClass("lg:grid-cols-4");
    const cards = within(list).getAllByRole("listitem");
    expect(cards).toHaveLength(4);
    const latency = cards[1];
    if (!latency) {
      throw new Error("no latency card");
    }
    expect(within(latency).getByText(/Upstream p95/)).toBeInTheDocument();
    expect(within(latency).getByLabelText(/No gateway overhead recorded/)).toHaveTextContent("—");
    expect(within(latency).getByText(/TTFT p95/)).toHaveTextContent("TTFT p95 640 ms");
  });
});

describe("windowPhrase", () => {
  it("keeps the number and its unit on one line", () => {
    expect(windowPhrase("24h")).toBe("last 24\u00a0h");
    expect(windowPhrase("7d")).toBe("last 7\u00a0d");
  });
});
