/** Shared API responses for the Attribution page tests. */
import { screen } from "@testing-library/react";
import { vi } from "vitest";

import {
  attributionApi,
  billingSourcesApi,
  projectsApi,
  type AttributionBucket,
  type AttributionResponse,
  type AttributionRow,
  type AttributionSeries,
  type AttributionTotals,
  type FilterOptions,
} from "@/lib/api";
import { makeProject, ORG_ID } from "@/test/render-app";

export const PROJECT_ID = "proj_1";
export const PAGE = `/${ORG_ID}/${PROJECT_ID}/attribution`;

export function figures(overrides: Partial<AttributionTotals> = {}): AttributionTotals {
  return {
    traces: 40,
    llm_calls: 90,
    errors: 0,
    unpriced_calls: 0,
    input_tokens: 9000,
    output_tokens: 400,
    cost_usd: "0.80000000",
    retries: 1,
    retry_cost_usd: "0.00100000",
    ...overrides,
  };
}

export function row(value: string, overrides: Partial<AttributionRow> = {}): AttributionRow {
  return { ...figures(), value, share_of_cost: 0.612, ...overrides };
}

export function bucket(overrides: Partial<AttributionBucket> = {}): AttributionBucket {
  return { ...figures(), folded_values: null, ...overrides };
}

export function response(overrides: Partial<AttributionResponse> = {}): AttributionResponse {
  return {
    group_by: "metadata",
    key: "customer_id",
    window: { from: "2026-09-16T00:00:00Z", to: "2026-10-16T00:00:00Z" },
    source: "rollup",
    updated_within_minutes: 15,
    computed_at: "2026-10-15T14:05:00Z",
    pending_traces: 0,
    items: [row("acme")],
    next_cursor: null,
    none: null,
    other: null,
    totals: figures({ traces: 1900, cost_usd: "100.00000000" }),
    ...overrides,
  };
}

/** A project of `ORG_ID` with the given promoted keys. */
export function project(promoted: string[] = []) {
  return { ...makeProject(PROJECT_ID, "Support"), promoted_metadata_keys: promoted };
}

const FILTERS: FilterOptions = {
  environments: ["production"],
  releases: [],
  models: [],
  tags: [],
  score_names: [],
  metadata_keys: ["customer_id", "plan", "region"],
  trace_names: [],
};

export function series(overrides: Partial<AttributionSeries> = {}): AttributionSeries {
  return {
    bucket: "day",
    source: "rollup",
    window: { from: "2026-10-14T00:00:00Z", to: "2026-10-16T00:00:00Z" },
    points: [
      { bucket_start: "2026-10-14T00:00:00Z", cost_usd: "4.12000000", unpriced_calls: 0 },
      { bucket_start: "2026-10-15T00:00:00Z", cost_usd: null, unpriced_calls: 3 },
    ],
    ...overrides,
  };
}

/**
 * The list read, plus the Spend chart's series and the Cost strip's reads (no billing source, so
 * the strip settles on "Not connected").
 */
export function mockAttribution(read: () => Promise<AttributionResponse>) {
  vi.spyOn(projectsApi, "filters").mockResolvedValue(FILTERS);
  vi.spyOn(projectsApi, "tokenMix").mockRejectedValue(new Error("not under test"));
  vi.spyOn(billingSourcesApi, "list").mockResolvedValue([]);
  vi.spyOn(attributionApi, "series").mockResolvedValue(series());
  return vi.spyOn(attributionApi, "read").mockImplementation(read);
}

/** The page's own list reads, without the Cost strip's one-row totals read (`limit: 1`). */
export function listReads(read: ReturnType<typeof mockAttribution>) {
  return read.mock.calls.filter(([, query]) => query.limit !== 1);
}

/** The window note's "Live" badges; the Cost strip's spend card can say "Live" too. */
export function windowNoteLive() {
  return screen
    .queryAllByText("Live")
    .filter((element) => element.closest("[aria-label='Cost summary']") === null);
}
