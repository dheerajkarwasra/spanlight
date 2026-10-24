import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  billingSourcesApi,
  type BillingSource,
  type ReconciliationReport,
} from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

vi.mock("@/features/attribution/cost-strip", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  CostStrip: () => null,
}));

// Whole UTC days in the past, so the window is the same whatever today is.
const RANGE = "range=custom&from=2026-01-10T00:00:00Z&to=2026-01-12T00:00:00Z";
const BASE = "/acme/checkout";

function source(id: string, name: string, projectIds: string[]): BillingSource {
  return {
    id,
    kind: "openai_costs_api",
    provider: "openai",
    name,
    provider_scope: null,
    enabled: true,
    status: "ok",
    key_set: true,
    key_id: null,
    last_fetched_at: "2026-01-12T03:30:00Z",
    last_success_at: "2026-01-12T03:30:00Z",
    last_error: null,
    projects: projectIds.map((id) => ({ id, name: id, slug: id })),
    created_by: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

const TOKENS = {
  uncached_input: 1,
  cache_read: 0,
  cache_write_5m: 0,
  cache_write_1h: 0,
  output: 1,
};

const REPORT: ReconciliationReport = {
  source: { id: "s1", provider: "openai", kind: "openai_costs_api" },
  window: { start: "2026-01-10T00:00:00Z", end: "2026-01-12T00:00:00Z" },
  projects: [
    { id: "proj_1", name: "Checkout" },
    { id: "proj_2", name: "Search" },
  ],
  summary: { days: 1, settled_days: 1, days_within_2pct: 0, max_abs_variance_ratio: "0.042" },
  days: [
    {
      day: "2026-01-10",
      provisional: false,
      fetched_at: "2026-01-12T03:30:00Z",
      incomplete: false,
      note: null,
      provider_usd: "100.00",
      spanlight_usd: "103.20",
      explained_usd: "1.00",
      residual_usd: "-4.20",
      variance_ratio: "-0.042",
      credits_usd: "0",
      other_usd: "0",
      unattributed_calls: 0,
      groups: [
        {
          group: "gpt-4.1",
          model_pattern: "gpt-4.1",
          provider_models: ["gpt-4.1"],
          spanlight_models: ["gpt-4.1-2025-04-14", "gpt-4.1"],
          provider_usd: "100.00",
          spanlight_usd: "103.20",
          explained_usd: "1.00",
          residual_usd: "-4.20",
          variance_ratio: "-0.042",
          provider_tokens: TOKENS,
          spanlight_tokens: TOKENS,
          explained: [],
          note: null,
        },
      ],
      credits: [],
      other: [],
    },
  ],
};

beforeEach(() => {
  window.localStorage.clear();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
});

describe("ReconciliationTabPage", () => {
  it("lists the sources that map this project, each linking to its report", async () => {
    vi.spyOn(billingSourcesApi, "list").mockResolvedValue([
      source("s1", "OpenAI prod", ["proj_1", "proj_2"]),
      source("s2", "Other team", ["proj_9"]),
    ]);
    vi.spyOn(billingSourcesApi, "reconciliation").mockResolvedValue(REPORT);
    renderApp(`/${ORG_ID}/proj_1/reconciliation?${RANGE}`);

    const table = await screen.findByRole("table", { name: "Billing sources for this project" });
    const row = (await within(table).findByText("$100.00")).closest('[role="row"]') as HTMLElement;
    const report = new URL(
      within(row).getByRole("link", { name: "OpenAI prod" }).getAttribute("href") ?? "",
      "http://x",
    );
    expect(report.pathname).toBe(`${BASE}/settings/billing-sources/s1`);
    expect(report.searchParams.get("first")).toBe("2026-01-10");
    expect(report.searchParams.get("last")).toBe("2026-01-11");
    expect(row).toHaveTextContent("openai");
    expect(row).toHaveTextContent("2 projects");
    expect(row).toHaveTextContent("Jan 10 – Jan 11 · 2 days");
    expect(row).toHaveTextContent("$103.20");
    expect(row).toHaveTextContent("−4.2 %");
    expect(row).toHaveTextContent("Within 10 %");
    expect(screen.queryByText("Other team")).toBeNull();
    expect(billingSourcesApi.reconciliation).toHaveBeenCalledWith(ORG_ID, "s1", {
      from: "2026-01-10T00:00:00Z",
      to: "2026-01-12T00:00:00Z",
    });
  });

  it("offers to connect a source when none covers the project", async () => {
    vi.spyOn(billingSourcesApi, "list").mockResolvedValue([source("s2", "Other", ["proj_9"])]);
    renderApp(`/${ORG_ID}/proj_1/reconciliation`);

    expect(await screen.findByText("No billing source covers this project.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a billing source" })).toHaveAttribute(
      "href",
      `${BASE}/settings/billing-sources`,
    );
  });

  it("asks for an admin when the organization's sources are hidden (403)", async () => {
    vi.spyOn(billingSourcesApi, "list").mockRejectedValue(new ApiError(403, {}));
    renderApp(`/${ORG_ID}/proj_1/reconciliation`);

    expect(
      await screen.findByText("Ask an organization admin to connect a billing source."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Connect a billing source" })).toBeNull();
  });

  it("shows a failed report as '—' on that row only, with the reason", async () => {
    vi.spyOn(billingSourcesApi, "list").mockResolvedValue([
      source("s1", "OpenAI prod", ["proj_1"]),
    ]);
    vi.spyOn(billingSourcesApi, "reconciliation").mockRejectedValue(new ApiError(422, {}));
    renderApp(`/${ORG_ID}/proj_1/reconciliation?${RANGE}`);

    const table = await screen.findByRole("table", { name: "Billing sources for this project" });
    const link = within(table).getByRole("link", { name: "OpenAI prod" });
    const row = link.closest('[role="row"]') as HTMLElement;
    expect(
      await within(row).findAllByLabelText("Unknown: Couldn't load billing data"),
    ).toHaveLength(3);
    expect(row).not.toHaveTextContent("$0.00");
  });

  it("gives Spanlight's figure its own reason when billed days have no priced call", async () => {
    vi.spyOn(billingSourcesApi, "list").mockResolvedValue([
      source("s1", "OpenAI prod", ["proj_1"]),
    ]);
    const [first] = REPORT.days;
    vi.spyOn(billingSourcesApi, "reconciliation").mockResolvedValue({
      ...REPORT,
      days: first ? [{ ...first, spanlight_usd: null, residual_usd: "99.00" }] : [],
    });
    renderApp(`/${ORG_ID}/proj_1/reconciliation?${RANGE}`);

    const table = await screen.findByRole("table", { name: "Billing sources for this project" });
    expect(
      await within(table).findByLabelText(/Spanlight recorded no priced call on these days/),
    ).toBeInTheDocument();
  });
});

describe("Reconciliation report links", () => {
  beforeEach(() => {
    vi.spyOn(billingSourcesApi, "get").mockResolvedValue(source("s1", "OpenAI prod", ["proj_1"]));
    vi.spyOn(billingSourcesApi, "reconciliation").mockResolvedValue(REPORT);
  });

  it("leads back to Reconciliation, to the source's settings and to a group's traces", async () => {
    const user = userEvent.setup();
    renderApp(`/${ORG_ID}/proj_1/settings/billing-sources/s1?first=2026-01-10&last=2026-01-11`);

    const crumbs = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Reconciliation" })).toHaveAttribute(
      "href",
      `${BASE}/reconciliation`,
    );
    expect(screen.getByRole("link", { name: "Manage source" })).toHaveAttribute(
      "href",
      `${BASE}/settings/billing-sources`,
    );

    await user.click(await screen.findByRole("button", { name: /^Jan 10\b/ }));
    const traces = screen.getByRole("link", { name: /^View traces for gpt-4\.1 on Jan 10/ });
    const href = new URL(traces.getAttribute("href") ?? "", "http://x");
    expect(href.pathname).toBe(`${BASE}/traces`);
    expect(Object.fromEntries(href.searchParams)).toEqual({
      range: "custom",
      from: "2026-01-10T00:00Z",
      to: "2026-01-11T00:00Z",
      model: "gpt-4.1-2025-04-14",
    });
    expect(screen.getByText("+1 model")).toBeInTheDocument();
  });

  it("links Settings › Billing sources to the Reconciliation tab", async () => {
    vi.spyOn(billingSourcesApi, "list").mockResolvedValue([source("s1", "OpenAI prod", [])]);
    renderApp(`/${ORG_ID}/proj_1/settings/billing-sources`);

    expect(await screen.findByRole("link", { name: "View reconciliation" })).toHaveAttribute(
      "href",
      `${BASE}/reconciliation`,
    );
  });
});
