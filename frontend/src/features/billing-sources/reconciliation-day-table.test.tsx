import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ReconciliationReport } from "@/lib/api";

import { DayTable } from "./reconciliation-day-table";
import { flattenDays } from "./reconciliation-table";

const TOKENS = {
  uncached_input: 10,
  cache_read: 0,
  cache_write_5m: 0,
  cache_write_1h: 0,
  output: 4,
};

const REPORT: ReconciliationReport = {
  source: { id: "src_1", provider: "anthropic", kind: "anthropic_admin_api" },
  window: { start: "2026-10-01T00:00:00Z", end: "2026-10-03T00:00:00Z" },
  projects: [{ id: "p1", name: "Support Copilot" }],
  summary: { days: 2, settled_days: 1, days_within_2pct: 1, max_abs_variance_ratio: "0.0037" },
  days: [
    {
      day: "2026-10-01",
      provisional: false,
      fetched_at: "2026-10-04T03:30:00Z",
      incomplete: false,
      note: null,
      provider_usd: "12.3456",
      spanlight_usd: "12.2000",
      explained_usd: "0.1000",
      residual_usd: "0.0456",
      variance_ratio: "0.0037",
      credits_usd: "-5.0000",
      other_usd: "0",
      unattributed_calls: 0,
      groups: [
        {
          group: "claude-sonnet-4-6",
          model_pattern: "claude-sonnet-4-6",
          provider_models: ["claude-sonnet-4-6"],
          spanlight_models: ["claude-sonnet-4-6-20260301"],
          provider_usd: "12.3456",
          spanlight_usd: "12.2000",
          explained_usd: "0.1000",
          residual_usd: "0.0456",
          variance_ratio: "0.0037",
          provider_tokens: TOKENS,
          spanlight_tokens: TOKENS,
          explained: [
            {
              kind: "server_tool_fees",
              usd: "0.1000",
              count: 10,
              tokens: null,
              note: "10 web searches on the provider side, 0 recorded",
            },
          ],
          note: null,
        },
      ],
      credits: [{ description: "Promotional credit", usd: "-5.0000" }],
      other: [],
    },
    {
      day: "2026-10-02",
      provisional: true,
      fetched_at: null,
      incomplete: false,
      note: null,
      provider_usd: null,
      spanlight_usd: "2.3100",
      explained_usd: null,
      residual_usd: null,
      variance_ratio: null,
      credits_usd: null,
      other_usd: null,
      unattributed_calls: 0,
      groups: [],
      credits: [],
      other: [],
    },
  ],
};

function renderTable() {
  render(
    <TooltipProvider delayDuration={0}>
      <DayTable rows={flattenDays(REPORT, "p_other")} />
    </TooltipProvider>,
  );
}

describe("DayTable (narrow: one disclosure tile per day)", () => {
  it("opens a day's model groups, explained lines and credits", async () => {
    const user = userEvent.setup();
    renderTable();
    const day = screen.getByRole("button", { name: /^Oct 1\b/ });
    expect(day).toHaveAttribute("aria-expanded", "false");

    await user.click(day);

    expect(day).toHaveAttribute("aria-expanded", "true");
    const detail = document.getElementById(day.getAttribute("aria-controls") ?? "");
    expect(detail).not.toBeNull();
    const scope = within(detail as HTMLElement);
    expect(scope.getByRole("heading", { name: "claude-sonnet-4-6" })).toBeInTheDocument();
    expect(scope.getByText("Server-tool fees")).toBeInTheDocument();
    expect(scope.getByText("Promotional credit")).toBeInTheDocument();
    expect(scope.getByText("−$5.00")).toBeInTheDocument();
  });

  it("reads an unknown figure's reason with the day, never a zero", () => {
    renderTable();
    const day = screen.getByRole("button", { name: /^Oct 2\b/ });
    expect(day).toHaveAccessibleName(expect.stringContaining("Not fetched yet") as string);
    expect(day).toHaveAccessibleName(expect.stringContaining("Provisional") as string);
    expect(within(day).queryByText("$0.00")).toBeNull();
  });
});
