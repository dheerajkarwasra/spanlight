import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError, costExplanationApi, type CostExplanation, type Span } from "@/lib/api";

import { SpanCostCard } from "./span-cost-card";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const span: Span = {
  span_id: "9f3c2a1d7b6e4c01",
  parent_span_id: null,
  kind: "llm",
  name: "chat claude-sonnet-4-6",
  status: "ok",
  status_message: null,
  error_class: null,
  provider_error_type: null,
  provider_error_code: null,
  rate_limit_kind: null,
  started_at: "2026-10-16T10:00:00Z",
  ended_at: "2026-10-16T10:00:01Z",
  duration_ms: 1000,
  provider: "anthropic",
  model: "claude-sonnet-4-6",
  input_tokens: 60510,
  output_tokens: 812,
  cached_tokens: 48200,
  cache_write_tokens: 0,
  cache_write_1h_tokens: 0,
  service_tier: "standard",
  web_search_requests: null,
  cost_usd: "0.06357000",
  fee_usd: null,
  pricing_version: "2026-10-16",
  time_to_first_token_ms: 412,
  input: null,
  output: null,
  attributes: {},
  truncated: false,
  finish_reason: null,
  prompt_version_id: null,
  usage_rollup: false,
  usage_state: "priced",
};

/** The spec §8.1 example response. */
const OK: CostExplanation = {
  span_id: span.span_id,
  trace_id: "trace_1",
  status: "ok",
  provider: "anthropic",
  provider_canonical: "anthropic",
  model: "claude-sonnet-4-6",
  service_tier: "standard",
  tier_multiplier: "1",
  tier_note: null,
  price: {
    source: "seed",
    version: "2026-10-16",
    model_pattern: "claude-sonnet-4-6",
    effective_from: "2026-10-16T00:00:00Z",
    min_prompt_tokens: 0,
    override_id: null,
  },
  usage: {
    input_tokens: 60510,
    output_tokens: 812,
    cached_tokens: 48200,
    cache_write_tokens: 0,
    cache_write_1h_tokens: 0,
    web_search_requests: null,
    reasoning_tokens: 300,
    unreported_reason: null,
  },
  lines: [
    line("uncached_input", 12310, "3.000000", "0.03693000"),
    line("cache_read", 48200, "0.300000", "0.01446000"),
    line("cache_write_5m", 0, "3.750000", "0"),
    line("cache_write_1h", 0, "6.000000", "0"),
    line("output", 812, "15.000000", "0.01218000"),
  ],
  output_breakdown: { reasoning: 300, other: 512 },
  recomputed_usd: "0.06357000",
  stored_usd: "0.06357000",
  stored_fee_usd: null,
  message: null,
  original_usage: null,
};

function line(
  kind: CostExplanation["lines"][number]["kind"],
  quantity: number,
  rate: string,
  usd: string,
): CostExplanation["lines"][number] {
  return {
    kind,
    quantity,
    unit: "tokens",
    rate,
    rate_unit: "usd_per_mtok",
    multiplier: "1",
    usd,
  };
}

function renderCard(spanOverrides: Partial<Span> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <SpanCostCard span={{ ...span, ...spanOverrides }} traceId="trace_1" />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

async function expand() {
  await userEvent.click(screen.getByRole("button", { name: "Show cost breakdown" }));
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SpanCostCard", () => {
  it("loads nothing until expanded and shows the stored cost in the header", () => {
    const get = vi.spyOn(costExplanationApi, "get").mockResolvedValue(OK);
    renderCard();
    expect(get).not.toHaveBeenCalled();
    const header = cardHeader();
    expect(header).toHaveTextContent("Cost breakdown·$0.063570");
  });

  it("keeps the status chip in the collapsed header once loaded", async () => {
    vi.spyOn(costExplanationApi, "get").mockResolvedValue(OK);
    renderCard();
    await expand();
    await screen.findByText("Uncached input");
    await userEvent.click(screen.getByRole("button", { name: "Hide cost breakdown" }));
    const header = cardHeader();
    expect(header).toHaveTextContent("Cost breakdown·$0.063570·Matches");
    expect(screen.queryByText("Uncached input")).not.toBeInTheDocument();
  });

  it("shows the five lines, the totals and the price row when expanded", async () => {
    const get = vi.spyOn(costExplanationApi, "get").mockResolvedValue(OK);
    renderCard();
    await expand();

    const table = await screen.findByRole("table", { name: "Cost lines" });
    expect(get).toHaveBeenCalledWith("proj_1", "trace_1", span.span_id);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      "Uncached input12,310$3.00 / MTok×1$0.036930",
      "Cache read48,200$0.30 / MTok×1$0.014460",
      "Cache write0$3.75 / MTok×1$0",
      "Cache write · 1 h0$6.00 / MTok×1$0",
      "Output812$15.00 / MTok×1$0.012180",
    ]);
    expect(rows[2]).toHaveClass("text-subtle-foreground");
    expect(rows[0]).not.toHaveClass("text-subtle-foreground");

    expect(screen.getByRole("status")).toHaveTextContent(
      "Recomputed from stored usage and price row 2026-10-16",
    );
    expect(screen.getByText("Recomputed").nextSibling).toHaveTextContent("$0.063570");
    expect(screen.getByText("Stored on the span").nextSibling).toHaveTextContent("$0.063570");
    expect(
      screen.getByText("Output 812 includes 300 reasoning tokens, billed at the output rate."),
    ).toBeInTheDocument();

    const footer = screen.getByRole("group", { name: "Pricing" });
    expect(field(footer, "Price row")).toBe("claude-sonnet-4-6");
    expect(field(footer, "Source")).toBe("seed");
    expect(field(footer, "Price version")).toBe("2026-10-16");
    expect(field(footer, "Service tier")).toBe("standard · ×1");
    expect(field(footer, "Effective from")).toBe("2026-10-16");
    expect(field(footer, "Span id")).toBe(span.span_id);
  });

  it("warns on a mismatch with both figures", async () => {
    vi.spyOn(costExplanationApi, "get").mockResolvedValue({
      ...OK,
      status: "mismatch",
      stored_usd: "0.06120000",
      message: "Recomputed 0.06357000 differs from stored 0.06120000.",
    });
    renderCard({ cost_usd: "0.06120000" });
    await expand();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Recomputed $0.063570, but the span stores $0.061200");
    expect(alert).toHaveTextContent("This is a bug in Spanlight. Report it with the span id.");
    expect(within(alert).getByRole("button", { name: "Copy span id" })).toBeInTheDocument();
    expect(screen.getByText("Mismatch")).toBeInTheDocument();
  });

  it("explains a span without usage with the server's sentence and no lines", async () => {
    const message =
      "Output tokens were not reported: the client disconnected, so the cost is unknown.";
    vi.spyOn(costExplanationApi, "get").mockResolvedValue({
      ...OK,
      status: "no_usage",
      usage: { ...OK.usage, output_tokens: null, unreported_reason: "client_disconnected" },
      lines: [],
      output_breakdown: null,
      recomputed_usd: null,
      stored_usd: null,
      message,
    });
    renderCard({ output_tokens: null, cost_usd: null });
    await expand();

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("No usage")).toBeInTheDocument();
    expect(screen.getByText("client disconnected")).toBeInTheDocument();
  });

  it("shows a cache hit with no price row and no lines", async () => {
    vi.spyOn(costExplanationApi, "get").mockResolvedValue({
      ...OK,
      status: "cache_hit",
      price: null,
      lines: [],
      output_breakdown: null,
      recomputed_usd: "0.00000000",
      stored_usd: "0.00000000",
      message: "Gateway cache hit: the response was served from the cache, so the cost is 0.",
      original_usage: {
        input_tokens: 1200,
        output_tokens: 80,
        cached_tokens: null,
        cache_write_tokens: null,
        cache_write_1h_tokens: null,
      },
    });
    renderCard({ cost_usd: "0" });
    await expand();

    expect(
      await screen.findByText(
        "Gateway cache hit: the response was served from the cache, so the cost is 0.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("Cache hit")).toBeInTheDocument();
    expect(screen.getByText("1,200")).toBeInTheDocument();
  });

  it("shows a loading skeleton while the explanation loads", async () => {
    vi.spyOn(costExplanationApi, "get").mockReturnValue(new Promise(() => {}));
    renderCard();
    await expand();
    expect(screen.getByRole("status")).toHaveTextContent("Loading the cost explanation…");
    expect(screen.getByTestId("span-cost-body")).toHaveAttribute("aria-busy", "true");
  });

  it("shows an error with a retry that refetches", async () => {
    let resolveRetry: (value: CostExplanation) => void = () => {};
    const retry = new Promise<CostExplanation>((resolve) => {
      resolveRetry = resolve;
    });
    const get = vi
      .spyOn(costExplanationApi, "get")
      .mockRejectedValueOnce(new ApiError(500, {}))
      .mockReturnValueOnce(retry);
    renderCard();
    await expand();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load the cost breakdown");
    expect(alert).toHaveTextContent(
      "The request failed. The span's stored cost above is unchanged; try again.",
    );
    const tryAgain = within(alert).getByRole("button", { name: "Try again" });
    await userEvent.click(tryAgain);
    await waitFor(() => {
      expect(get).toHaveBeenCalledTimes(2);
    });
    // While the retry is in flight the card is busy: the loading state replaces the error.
    expect(screen.getByTestId("span-cost-body")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Loading the cost explanation…");
    expect(tryAgain).not.toBeInTheDocument();
    resolveRetry(OK);
    expect(await screen.findByRole("table", { name: "Cost lines" })).toBeInTheDocument();
  });
});

describe("SpanCostCard details", () => {
  it("toggles aria-expanded and names the button for the next action", async () => {
    vi.spyOn(costExplanationApi, "get").mockResolvedValue(OK);
    renderCard();
    const toggle = screen.getByRole("button", { name: "Show cost breakdown" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAccessibleName("Hide cost breakdown");
    expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")).not.toBeNull();
  });

  it("links a span with no price to the docs on unpriced models and overrides", async () => {
    vi.spyOn(costExplanationApi, "get").mockResolvedValue({
      ...OK,
      status: "no_price",
      price: null,
      lines: [],
      output_breakdown: null,
      recomputed_usd: null,
      stored_usd: null,
      message:
        "No price matched anthropic claude-sonnet-4-6 when the span was ingested, so the cost is unknown.",
    });
    renderCard({ cost_usd: null, pricing_version: null });
    await expand();

    expect(
      await screen.findByText(
        "No price matched anthropic claude-sonnet-4-6 when the span was ingested, so the cost is unknown. Usage was recorded: 60,510 input, 48,200 cached, 812 output.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See unpriced models" })).toHaveAttribute(
      "href",
      "/docs/concepts/cost/#when-a-cost-is-unknown",
    );
    expect(screen.getByRole("link", { name: "Add a price override" })).toHaveAttribute(
      "href",
      "/docs/concepts/cost/#price-overrides",
    );
  });

  it("shows a deleted override's stored figure with the row as unknown", async () => {
    vi.spyOn(costExplanationApi, "get").mockResolvedValue({
      ...OK,
      status: "override_deleted",
      price: null,
      lines: [],
      output_breakdown: null,
      recomputed_usd: null,
      message: "The price override that priced this span (override:4c1e9a2f) was deleted.",
    });
    renderCard({ pricing_version: "override:4c1e9a2f" });
    await expand();

    const footer = await screen.findByRole("group", { name: "Pricing" });
    expect(field(footer, "Source")).toBe("override (deleted)");
    expect(field(footer, "Price row")).toBe("—");
    expect(field(footer, "Price version")).toBe("override:4c1e9a2f");
    expect(screen.getByText("Price row gone")).toBeInTheDocument();
    expect(cardHeader()).toHaveTextContent("$0.063570");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says which price version is gone when its row left the price table", async () => {
    vi.spyOn(costExplanationApi, "get").mockResolvedValue({
      ...OK,
      status: "price_version_missing",
      price: null,
      lines: [],
      output_breakdown: null,
      recomputed_usd: null,
      message: null,
    });
    renderCard({ pricing_version: "2026-09-01" });
    await expand();

    const footer = await screen.findByRole("group", { name: "Pricing" });
    expect(within(footer).getAllByLabelText(/^Unknown: /)[0]).toHaveAccessibleName(
      "Unknown: The price row that priced this span (version 2026-09-01) is no longer in the price table; the stored figure is shown as it was priced.",
    );
  });

  it("adds a web search fee line without a tier multiplier", async () => {
    vi.spyOn(costExplanationApi, "get").mockResolvedValue({
      ...OK,
      lines: [
        ...OK.lines,
        {
          kind: "web_search",
          quantity: 3,
          unit: "requests",
          rate: "10.00",
          rate_unit: "usd_per_1k_requests",
          multiplier: "1",
          usd: "0.03000000",
        },
      ],
    });
    renderCard();
    await expand();

    const table = await screen.findByRole("table", { name: "Cost lines" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(7);
    expect(rows[6]).toHaveTextContent("Web search3$10.00 / 1K requests$0.030000");
    expect(rows[6]).not.toHaveTextContent("×");
  });
});

/** The row that holds the card title, the figure and the toggle. */
function cardHeader(): HTMLElement {
  const header = screen.getByRole("heading", { name: "Cost breakdown" }).parentElement;
  if (header === null) {
    throw new Error("no card header");
  }
  return header;
}

/** The text of one `dt`/`dd` pair inside `container`. */
function field(container: HTMLElement, label: string): string | null {
  const term = within(container).getByText(label, { selector: "dt" });
  return term.nextElementSibling?.textContent ?? null;
}
