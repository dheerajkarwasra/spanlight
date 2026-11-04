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

import { enforcementNote } from "./budget-enforcement";

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
    reserved_usd: null,
    gateway_settled_usd: null,
  },
  created_by: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

const BURN: BudgetBurn = {
  period_start: "2026-10-01T00:00:00Z",
  resets_at: "2026-11-01T00:00:00Z",
  amount_usd: "500.0000",
  forecast_usd: null,
  forecast_basis_days: 0,
  forecast_crosses_on: null,
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

  it("states how blocking works once, in the footnote, not in the empty state", async () => {
    vi.spyOn(budgetsApi, "list").mockResolvedValue([]);
    renderApp(`/${ORG_ID}/proj_1/budgets`);

    expect(await screen.findByText("No budgets")).toBeInTheDocument();
    expect(screen.getAllByText(enforcementNote(), { exact: false })).toHaveLength(1);
  });

  it("shows the gateway spend in flight for block budgets only, and drops 'soft' for a gateway key", async () => {
    const state = BUDGET.state;
    vi.spyOn(budgetsApi, "list").mockResolvedValue([
      BUDGET,
      {
        ...BUDGET,
        id: "bud_2",
        name: "prod key cap",
        scope: "gateway_key",
        scope_id: "key_1",
        action: "block",
        state: state && {
          ...state,
          state: "firing",
          reserved_usd: "0.4200",
          gateway_settled_usd: "0",
        },
      },
      { ...BUDGET, id: "bud_3", name: "fresh cap", action: "block", state: null },
    ]);
    renderApp(`/${ORG_ID}/proj_1/budgets`);

    const keyRow = (await screen.findByText("prod key cap")).closest("li") as HTMLElement;
    expect(within(keyRow).getByText("$0.42")).toBeInTheDocument();
    expect(within(keyRow).getByText("Blocking")).toBeInTheDocument();
    expect(within(keyRow).queryByText(/soft/i)).not.toBeInTheDocument();
    // Never evaluated: the in-flight figure is unknown, not $0.00.
    const fresh = screen.getByText("fresh cap").closest("li") as HTMLElement;
    const freshLine = within(fresh).getByText("In flight").parentElement as HTMLElement;
    expect(
      within(freshLine).getByLabelText("Unknown: Not evaluated yet. Spend shows within a minute."),
    ).toHaveTextContent("—");
    expect(within(fresh).queryByText("$0.00")).not.toBeInTheDocument();
    // A notify budget has no gateway counter, so no in-flight line.
    const notify = screen.getByText("app-agent monthly").closest("li") as HTMLElement;
    expect(within(notify).queryByText("In flight")).not.toBeInTheDocument();
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

  it("shows a refused block budget's retention requirement under Action, not as a toast", async () => {
    vi.spyOn(budgetsApi, "list").mockResolvedValue([]);
    vi.spyOn(budgetsApi, "create").mockRejectedValue(
      new ApiError(422, {
        code: "BUDGET_HISTORY_RETENTION",
        detail: "An enabled block budget requires at least 31 days of project retention.",
      }),
    );
    renderApp(`/${ORG_ID}/proj_1/budgets?new=1`);
    const user = userEvent.setup();

    const dialog = await screen.findByRole("dialog", { name: "Create budget" });
    await user.type(within(dialog).getByLabelText("Name"), "Monthly cap");
    await user.type(within(dialog).getByLabelText("Amount (USD)"), "50");
    await user.click(within(dialog).getByRole("radio", { name: "Block" }));
    expect(dialog).toHaveTextContent("at least 31 days of project retention");
    await user.click(within(dialog).getByRole("button", { name: "Create budget" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "An enabled block budget requires at least 31 days of project retention.",
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
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
