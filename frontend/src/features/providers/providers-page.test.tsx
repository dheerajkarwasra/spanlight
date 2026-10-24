import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  providersApi,
  type HealthReport,
  type IncidentList,
  type ProviderHealth,
} from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PATH = `/${ORG_ID}/proj_1/providers`;

const ANTHROPIC: ProviderHealth = {
  provider: "anthropic",
  llm_calls: 8412,
  errors: 263,
  error_rate: "0.031265",
  by_class: [{ error_class: "rate_limit", count: 166 }],
  rate_limit_kinds: [{ kind: "tokens", count: 92, with_retry_after: 92 }],
  retry_calls: 212,
  retry_cost_usd: "1.9412",
  retry_cost_unknown: false,
  incidents: [],
};

function report(providers: ProviderHealth[]): HealthReport {
  return {
    window: { start: "2026-10-15T05:30:00Z", end: "2026-10-16T05:30:00Z" },
    approximate: false,
    providers,
    retry_cost_note: "Retries are counted within each trace.",
  };
}

const INCIDENTS: IncidentList = {
  polling: "enabled",
  sources: [],
  items: [],
  next_cursor: null,
};

function mockProviders(health: () => Promise<HealthReport>, incidents: IncidentList = INCIDENTS) {
  vi.spyOn(providersApi, "health").mockImplementation(health);
  vi.spyOn(providersApi, "errorTimeseries").mockResolvedValue([]);
  vi.spyOn(providersApi, "samples").mockResolvedValue({
    items: [],
    next_cursor: null,
    clamped: false,
  });
  vi.spyOn(providersApi, "incidents").mockResolvedValue(incidents);
}

describe("ProvidersPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
  });

  it("announces loading while provider health is on its way", async () => {
    mockProviders(() => new Promise(() => undefined));
    renderApp(PATH);

    expect(await screen.findByRole("heading", { name: "Provider health" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading provider health");
  });

  it("says when the window holds no LLM calls", async () => {
    mockProviders(() => Promise.resolve(report([])));
    renderApp(PATH);

    expect(await screen.findByText("No LLM calls in this window")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show last 7 days" })).toBeInTheDocument();
  });

  it("shows the failure with a retry", async () => {
    mockProviders(() => Promise.reject(new ApiError(422, { detail: "The window is too long." })));
    renderApp(PATH);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Provider health didn’t load")).toBeInTheDocument();
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says the status feeds are off when polling is disabled", async () => {
    mockProviders(() => Promise.resolve(report([ANTHROPIC])), {
      ...INCIDENTS,
      polling: "disabled",
    });
    renderApp(PATH);

    expect(
      await screen.findByText(/Provider status feeds are off on this server/),
    ).toBeInTheDocument();
    expect(screen.getByText("PROVIDER_STATUS_POLLING_ENABLED=false")).toBeInTheDocument();
  });

  it("renders a provider card and the retry cost", async () => {
    mockProviders(() => Promise.resolve(report([ANTHROPIC])));
    renderApp(PATH);

    const card = await screen.findByRole("region", { name: "Anthropic" });
    expect(within(card).getByText("8,412")).toBeInTheDocument();
    expect(within(card).getByText("3.13 %")).toBeInTheDocument();
    expect(within(card).getByText("Token limit")).toBeInTheDocument();
    expect(screen.getByText("$1.94")).toBeInTheDocument();
  });

  it("shows the window once, in the top bar", async () => {
    mockProviders(() => Promise.resolve(report([ANTHROPIC])));
    renderApp(PATH);

    expect(await screen.findByRole("region", { name: "Anthropic" })).toBeInTheDocument();
    expect(screen.queryByText(/· to /)).not.toBeInTheDocument();
  });

  it("collapses a window without failed calls to one line and keeps incidents", async () => {
    mockProviders(() =>
      Promise.resolve(
        report([{ ...ANTHROPIC, errors: 0, error_rate: "0", by_class: [], rate_limit_kinds: [] }]),
      ),
    );
    renderApp(PATH);

    expect(await screen.findByText("No failed calls in this window.")).toBeInTheDocument();
    expect(screen.queryByText("Errors by class")).not.toBeInTheDocument();
    expect(screen.getByText("Provider incidents")).toBeInTheDocument();
  });

  it("labels the provider-less row No provider and names its causes", async () => {
    mockProviders(() =>
      Promise.resolve(
        report([
          {
            ...ANTHROPIC,
            provider: null,
            rate_limit_kinds: [{ kind: "gateway", count: 4, with_retry_after: 0 }],
          },
        ]),
      ),
    );
    renderApp(PATH);

    const card = await screen.findByRole("region", { name: "No provider" });
    expect(
      within(card).getByText(
        "Gateway cache hits, gateway refusals and spans sent without a provider",
      ),
    ).toBeInTheDocument();
    // The rate-limit kinds table says "No provider" too, never "unknown".
    expect(screen.queryByText("unknown")).not.toBeInTheDocument();
    expect(screen.getAllByText("No provider").length).toBeGreaterThan(1);
  });

  it("formats a tiny known retry cost like every other cost, never as $0", async () => {
    mockProviders(() => Promise.resolve(report([{ ...ANTHROPIC, retry_cost_usd: "0.00003" }])));
    renderApp(PATH);

    const tile = await screen.findByRole("region", { name: "Retry cost" });
    expect(within(tile).getAllByText(/\$0\.000030/).length).toBeGreaterThan(0);
    expect(within(tile).queryByText("$0.0000")).not.toBeInTheDocument();
  });

  it("shows an unknown retry cost as a dash with its reason", async () => {
    mockProviders(() =>
      Promise.resolve(report([{ ...ANTHROPIC, retry_cost_usd: null, retry_cost_unknown: true }])),
    );
    renderApp(PATH);

    // The tile's total and the provider's line in its breakdown are both unknown.
    const dashes = await screen.findAllByLabelText("Unknown: a retried call has no known cost");
    expect(dashes[0]).toHaveTextContent("—");
    expect(screen.getByText(/212 retried calls in the window/)).toBeInTheDocument();
  });

  it("lists classes, samples and incidents, and switches the chart to its table", async () => {
    mockProviders(() => Promise.resolve(report([ANTHROPIC])), {
      polling: "enabled",
      sources: [
        {
          provider: "anthropic",
          last_success_at: "2020-01-01T00:00:00Z",
          last_polled_at: "2020-01-01T00:00:00Z",
          last_outcome: "fetched",
          consecutive_failures: 3,
        },
      ],
      items: [
        {
          id: "inc_1",
          provider: "anthropic",
          title: "<b>claude.ai</b> login errors",
          impact: "minor",
          status: "resolved",
          api_affected: false,
          components: ["claude.ai"],
          started_at: "2026-10-15T18:02:00Z",
          resolved_at: "2026-10-15T18:40:00Z",
          url: "https://status.claude.com/incidents/1",
          latest_update: null,
        },
        {
          id: "inc_2",
          provider: "openai",
          title: "Not a status page link",
          impact: "major",
          status: "investigating",
          api_affected: true,
          components: ["API"],
          started_at: "2026-10-16T04:40:00Z",
          resolved_at: null,
          url: "https://example.com/incidents/2",
          latest_update: null,
        },
      ],
      next_cursor: null,
    });
    vi.spyOn(providersApi, "errorTimeseries").mockResolvedValue([
      {
        bucket_start: "2026-10-16T01:00:00Z",
        by_class: { rate_limit: 12, timeout: 4 },
        approximate: false,
      },
      { bucket_start: "2026-10-16T02:00:00Z", by_class: {}, approximate: false },
    ]);
    vi.spyOn(providersApi, "samples").mockResolvedValue({
      items: [
        {
          trace_id: "trace_1",
          span_id: "span_1",
          started_at: "2026-10-16T05:27:41Z",
          model: "claude-sonnet-4-5",
          status_message: null,
          error_class: "rate_limit",
          rate_limit_kind: "tokens",
          provider_error_type: "rate_limit_error",
          provider_error_code: null,
          retry_after_s: 12,
          attempts: 3,
          request_id: null,
        },
        {
          trace_id: "trace_2",
          span_id: "span_2",
          started_at: "not a time",
          model: null,
          status_message: null,
          error_class: "timeout",
          rate_limit_kind: null,
          provider_error_type: null,
          provider_error_code: null,
          retry_after_s: null,
          attempts: null,
          request_id: null,
        },
      ],
      next_cursor: null,
      clamped: true,
    });
    renderApp(PATH);

    const legend = await screen.findByRole("list", { name: "Error classes" });
    expect(within(legend).getByText("Rate limited")).toBeInTheDocument();
    expect(within(legend).getByText("12")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show table" }));
    expect(screen.getByRole("button", { name: "Show chart" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Oct 16 · 01:00 – 02:00" })).toBeInTheDocument();
    // The table's scroll box is one keyboard-reachable region.
    const scroller = screen.getByRole("region", { name: /class, table$/ });
    expect(scroller).toHaveAttribute("tabindex", "0");
    expect(window.localStorage.getItem("spanlight-provider-errors-view")).toBe("table");

    const sample = screen.getByRole("link", { name: /^Open trace: 05:27:41 UTC/ });
    expect(sample).toHaveAttribute("href", expect.stringContaining("/traces/trace_1"));
    expect(sample).toHaveAttribute("href", expect.stringContaining("span=span_1"));
    // The kind reads as its label, with the stored value under it.
    expect(sample).toHaveAccessibleName(expect.stringContaining("kind Token limit"));
    expect(within(sample).getByText("Token limit")).toBeInTheDocument();
    expect(within(sample).getByText("tokens")).toHaveClass("font-mono");
    // The section title states its own 24-hour scan window; no warning callout.
    expect(screen.getByText("Newest failures · last 24 hours")).toBeInTheDocument();
    expect(screen.queryByText(/samples from the last 24 hours/)).not.toBeInTheDocument();
    // A start time that does not parse renders a dash instead of throwing.
    expect(screen.getByRole("link", { name: /^Open trace: time unknown/ })).toBeInTheDocument();

    // Titles are text, never markup; the incident did not touch the API.
    expect(screen.getByText("<b>claude.ai</b> login errors")).toBeInTheDocument();
    expect(screen.getByText("Not API")).toBeInTheDocument();
    // Only the status page URL becomes a link.
    const statusLinks = screen.getAllByRole("link", { name: /Status page/ });
    expect(statusLinks).toHaveLength(1);
    expect(statusLinks[0]).toHaveAttribute("href", "https://status.claude.com/incidents/1");
    expect(screen.getByText("Not a status page link")).toBeInTheDocument();
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByText(/3 failed polls/)).toBeInTheDocument();
  });
});
