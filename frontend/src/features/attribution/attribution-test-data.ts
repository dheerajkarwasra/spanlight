/** Shared API responses for the Attribution page tests. */
import { vi } from "vitest";

import {
  attributionApi,
  projectsApi,
  type AttributionBucket,
  type AttributionResponse,
  type AttributionRow,
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

export function mockAttribution(read: () => Promise<AttributionResponse>) {
  vi.spyOn(projectsApi, "filters").mockResolvedValue(FILTERS);
  return vi.spyOn(attributionApi, "read").mockImplementation(read);
}
