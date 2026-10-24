import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  alertsApi,
  ApiError,
  authApi,
  budgetsApi,
  gatewayApi,
  type Budget,
  type BudgetBurn,
} from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

import { softLimitNote } from "./budget-enforcement";

const BUDGET: Budget = {
  id: "bud_1",
  project_id: "proj_1",
  name: "app-agent monthly",
  scope: "project",
  scope_id: null,
  period: "monthly",
  amount_usd: "500.0000",
  action: "notify",
  enabled: true,
  channel_ids: [],
  state: {
    spent_usd: "312.4000",
    period_start: "2026-10-01T00:00:00Z",
    resets_at: "2026-11-01T00:00:00Z",
    state: "ok",
    last_evaluated_at: "2026-10-14T12:00:00Z",
  },
  created_by: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

const BURN: BudgetBurn = {
  period_start: "2026-10-01T00:00:00Z",
  resets_at: "2026-11-01T00:00:00Z",
  amount_usd: "500.0000",
  points: [
    { day: "2026-10-01", spend_usd: null, cumulative_usd: null },
    { day: "2026-10-14", spend_usd: "182.0000", cumulative_usd: "182.0000" },
  ],
};

describe("BudgetsPage", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(gatewayApi, "keys").mockResolvedValue([]);
    vi.spyOn(alertsApi, "channels").mockResolvedValue([]);
  });

  it("states the soft limit once, in the footnote, not in the empty state", async () => {
    vi.spyOn(budgetsApi, "list").mockResolvedValue([]);
    renderApp(`/${ORG_ID}/proj_1/budgets`);

    expect(await screen.findByText("No budgets")).toBeInTheDocument();
    expect(screen.getAllByText(softLimitNote(), { exact: false })).toHaveLength(1);
  });

  it("marks 50, 80 and 100 % on the progress bar, each named in its tooltip", async () => {
    vi.spyOn(budgetsApi, "list").mockResolvedValue([BUDGET]);
    renderApp(`/${ORG_ID}/proj_1/budgets`);
    const user = userEvent.setup();

    await screen.findByText("app-agent monthly");
    const ticks = document.querySelectorAll("[data-budget-tick]");
    expect([...ticks].map((tick) => tick.getAttribute("data-budget-tick"))).toEqual([
      "50",
      "80",
      "100",
    ]);
    await user.hover(ticks[1] as Element);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("80 % · $400");
    // The marks are hover-only on screen, so screen readers get them in words.
    expect(screen.getByText("Marks at 50 % $250, 80 % $400, 100 % $500.")).toHaveClass("sr-only");
  });

  it("opens the create dialog prefilled from the URL and clears the params on close", async () => {
    vi.spyOn(budgetsApi, "list").mockResolvedValue([]);
    const router = renderApp(
      `/${ORG_ID}/proj_1/budgets?new=1&scope=metadata&key=customer_id&value=acme&range=7d`,
    );
    const user = userEvent.setup();

    expect(await screen.findByRole("dialog", { name: "Create budget" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Metadata" })).toBeChecked();
    expect(screen.getByDisplayValue("customer_id")).toBeInTheDocument();
    expect(screen.getByDisplayValue("acme")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(router.state.location.search).toEqual({ range: "7d" });
  });

  it("drops the create params for a member, who can't create budgets", async () => {
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      memberships: ME.memberships.map((each) => ({ ...each, role: "member" as const })),
    });
    vi.spyOn(budgetsApi, "list").mockResolvedValue([]);
    const router = renderApp(
      `/${ORG_ID}/proj_1/budgets?new=1&scope=metadata&key=customer_id&value=acme&range=7d`,
    );

    expect(await screen.findByText("No budgets")).toBeInTheDocument();
    await waitFor(() => {
      expect(router.state.location.search).toEqual({ range: "7d" });
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("Budget burn", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(gatewayApi, "keys").mockResolvedValue([]);
    vi.spyOn(budgetsApi, "list").mockResolvedValue([BUDGET]);
  });

  it("is closed by default, then loads once and lists the days in the table view", async () => {
    const burn = vi.spyOn(budgetsApi, "burn").mockResolvedValue(BURN);
    renderApp(`/${ORG_ID}/proj_1/budgets`);
    const user = userEvent.setup();

    const toggle = await screen.findByRole("button", { name: "Show burn" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(burn).not.toHaveBeenCalled();

    await user.click(toggle);
    expect(screen.getByRole("button", { name: "Hide burn" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await user.click(await screen.findByRole("button", { name: "Show table" }));
    const table = screen.getByRole("table", { name: /Spend to date per day/ });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual(["Oct 1—", "Oct 14$182.0036 %"]);
    expect(burn).toHaveBeenCalledTimes(1);
    expect(burn).toHaveBeenCalledWith("proj_1", "bud_1");
  });

  it("shows an error state with a retry", async () => {
    vi.spyOn(budgetsApi, "burn").mockRejectedValue(new ApiError(500, {}));
    renderApp(`/${ORG_ID}/proj_1/budgets`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Show burn" }));
    expect(await screen.findByText("Couldn't load the burn line")).toBeInTheDocument();
  });
});
