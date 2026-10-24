import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { gatewayApi, type GatewayKey, type GatewayOverview } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PATH = `/${ORG_ID}/proj_1/gateway`;

function emptyOverview(): GatewayOverview {
  return {
    requests: 0,
    errors: 0,
    error_rate: null,
    cache: { hits: 0, misses: 0, hit_rate: null },
    fallbacks: 0,
    retries: 0,
    p95_overhead_ms: null,
    p95_ttft_ms: null,
    p95_upstream_ms: null,
    rate_limited: 0,
    previous: {
      requests: 0,
      errors: 0,
      error_rate: null,
      p95_overhead_ms: null,
      cache_hit_rate: null,
      fallbacks: 0,
      rate_limited: 0,
    },
    series: { bucket: "hour", points: [] },
    by_target: [],
    by_key: [],
    faults: [],
  };
}

describe("GatewayOverviewPage", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
  });

  it("says the window is empty and points at the keys when keys exist but nothing was sent", async () => {
    vi.spyOn(gatewayApi, "keys").mockResolvedValue([{ id: "key_1" } as unknown as GatewayKey]);
    vi.spyOn(gatewayApi, "overview").mockResolvedValue(emptyOverview());
    renderApp(PATH);

    expect(await screen.findByText("No requests in this window")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Your keys are ready. Point a client at the base URL and requests show up here.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View keys" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/gateway\/keys$/),
    );
    // One empty state replaces the zero tiles and empty tables.
    expect(screen.queryByRole("list", { name: "Gateway metrics" })).not.toBeInTheDocument();
  });

  it("shows the four cards, then requests over time, then the tables", async () => {
    vi.spyOn(gatewayApi, "keys").mockResolvedValue([{ id: "key_1" } as unknown as GatewayKey]);
    vi.spyOn(gatewayApi, "overview").mockResolvedValue({
      ...emptyOverview(),
      requests: 12,
      errors: 2,
      error_rate: 2 / 12,
      series: {
        bucket: "hour",
        points: [
          { bucket_start: "2026-10-20T13:00:00Z", requests: 5, errors: 0 },
          { bucket_start: "2026-10-20T14:00:00Z", requests: 7, errors: 2 },
        ],
      },
      by_key: [
        {
          key_id: "key_1",
          name: "checkout",
          environment: "production",
          requests: 12,
          errors: 2,
          cost_usd: null,
        },
      ],
    });
    renderApp(PATH);

    const cards = await screen.findByRole("list", { name: "Gateway metrics" });
    expect(within(cards).getAllByRole("listitem")).toHaveLength(4);
    const chart = screen.getByRole("region", { name: "Requests over time" });
    expect(within(chart).getByRole("img", { name: /12 requests, 2 errors/ })).toBeInTheDocument();
    expect(
      screen.getAllByLabelText(/No price for this model\. Set prices in Cost › Pricing\./).length,
    ).toBeGreaterThan(0);
  });

  it("never says the keys are ready before the keys have loaded", async () => {
    vi.spyOn(gatewayApi, "keys").mockReturnValue(new Promise(() => undefined));
    vi.spyOn(gatewayApi, "overview").mockResolvedValue(emptyOverview());
    renderApp(PATH);

    expect(await screen.findByRole("list", { name: "Gateway metrics" })).toBeInTheDocument();
    expect(screen.queryByText("No requests in this window")).not.toBeInTheDocument();
  });

  it("keeps the previous window dimmed, not 'No requests', while the next window loads", async () => {
    vi.spyOn(gatewayApi, "keys").mockResolvedValue([{ id: "key_1" } as unknown as GatewayKey]);
    const overview = vi
      .spyOn(gatewayApi, "overview")
      .mockResolvedValueOnce(emptyOverview())
      .mockReturnValue(new Promise(() => undefined));
    const router = renderApp(`${PATH}?range=24h`);
    expect(await screen.findByText("No requests in this window")).toBeInTheDocument();

    await act(() =>
      router.navigate({
        to: ".",
        search: (previous: Record<string, unknown>) => ({ ...previous, range: "7d" }),
      } as never),
    );
    await waitFor(() => {
      expect(overview).toHaveBeenCalledTimes(2);
    });

    // The zero belongs to the previous window: it says nothing about this one yet.
    expect(screen.queryByText("No requests in this window")).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Gateway metrics" })).toBeInTheDocument();
  });
});
