import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { attributionApi, authApi, budgetsApi } from "@/lib/api";
import { ME, mockSignedIn, renderApp } from "@/test/render-app";

import {
  bucket,
  mockAttribution,
  PAGE,
  project,
  response,
  row,
  series,
} from "./attribution-test-data";

const TRACE_NAMES = `${PAGE}?group_by=trace_name&range=30d`;

function traceNames() {
  return response({ group_by: "trace_name", key: null, items: [row("support-bot")] });
}

async function openRowMenu(value: string) {
  const table = within(await screen.findByRole("table", { name: "Cost by trace name" }));
  await userEvent.click(table.getByRole("button", { name: `Actions for ${value}` }));
  return within(await screen.findByRole("menu"));
}

function asMember() {
  vi.spyOn(authApi, "me").mockResolvedValue({
    ...ME,
    memberships: ME.memberships.map((each) => ({ ...each, role: "member" as const })),
  });
}

describe("Spend page", () => {
  beforeEach(() => {
    mockSignedIn([project(["customer_id"])]);
  });

  it("is titled Spend and charts the window's spend for all groups", async () => {
    mockAttribution(() => Promise.resolve(traceNames()));
    renderApp(TRACE_NAMES);

    expect(await screen.findByRole("heading", { level: 1, name: "Spend" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Spend over time" })).toBeInTheDocument();
    await waitFor(() => {
      expect(attributionApi.series).toHaveBeenCalledWith(
        "proj_1",
        expect.objectContaining({ group_by: "trace_name", value: undefined }),
      );
    });
    expect(screen.queryByText("Showing")).not.toBeInTheDocument();
  });

  it("charts one group from its row menu, and All groups clears it", async () => {
    mockAttribution(() => Promise.resolve(traceNames()));
    const router = renderApp(TRACE_NAMES);

    const menu = await openRowMenu("support-bot");
    await userEvent.click(menu.getByRole("menuitem", { name: "Show over time" }));

    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ focus: "support-bot" });
    });
    await waitFor(() => {
      expect(attributionApi.series).toHaveBeenLastCalledWith(
        "proj_1",
        expect.objectContaining({ value: "support-bot" }),
      );
    });
    const chart = within(screen.getByRole("region", { name: "Spend over time" }));
    expect(chart.getByText(/^Showing/)).toHaveTextContent("Showing support-bot");

    await userEvent.click(chart.getByRole("button", { name: "All groups" }));
    await waitFor(() => {
      expect(router.state.location.search).not.toHaveProperty("focus");
    });
    expect(chart.queryByText(/^Showing/)).not.toBeInTheDocument();
  });

  it("drops the focus when the grouping changes", async () => {
    mockAttribution(() => Promise.resolve(traceNames()));
    const router = renderApp(`${TRACE_NAMES}&focus=support-bot`);

    expect(await screen.findByText(/^Showing/)).toHaveTextContent("Showing support-bot");
    await userEvent.click(screen.getByRole("radio", { name: "User" }));

    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ group_by: "user" });
    });
    expect(router.state.location.search).not.toHaveProperty("focus");
  });

  it("offers an admin a budget prefilled for the group, and opens its traces", async () => {
    mockAttribution(() => Promise.resolve(traceNames()));
    vi.spyOn(budgetsApi, "list").mockResolvedValue([]);
    const router = renderApp(TRACE_NAMES);

    const menu = await openRowMenu("support-bot");
    const traces = menu.getByRole("menuitem", { name: "Open traces" });
    const tracesUrl = new URL(traces.getAttribute("href") ?? "", "http://localhost");
    expect(tracesUrl.searchParams.get("name")).toBe("support-bot");

    await userEvent.click(menu.getByRole("menuitem", { name: "Set a budget for this" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/acme/support/budgets");
    });
    expect(router.state.location.search).toMatchObject({
      new: "1",
      scope: "trace_name",
      value: "support-bot",
    });
  });

  it("offers no budget to a member", async () => {
    asMember();
    mockAttribution(() => Promise.resolve(traceNames()));
    renderApp(TRACE_NAMES);

    const menu = await openRowMenu("support-bot");
    expect(menu.getByRole("menuitem", { name: "Show over time" })).toBeInTheDocument();
    expect(menu.queryByRole("menuitem", { name: "Set a budget for this" })).toBeNull();
  });

  it("keeps the row menu in the sticky Group cell, with no Actions column", async () => {
    mockAttribution(() => Promise.resolve(traceNames()));
    renderApp(TRACE_NAMES);

    const table = within(await screen.findByRole("table", { name: "Cost by trace name" }));
    const trigger = await table.findByRole("button", { name: "Actions for support-bot" });
    expect(trigger.closest("[role='cell'], [role='rowheader']")).toBe(
      table.getByRole("link", { name: "support-bot" }).closest("[role='cell'], [role='rowheader']"),
    );
    expect(table.queryByRole("columnheader", { name: "Actions" })).toBeNull();
  });

  it("offers no budget for a release, which budgets can't scope", async () => {
    mockAttribution(() =>
      Promise.resolve(response({ group_by: "release", key: null, items: [row("v1.4.0")] })),
    );
    renderApp(`${PAGE}?group_by=release&range=30d`);

    const table = within(await screen.findByRole("table", { name: "Cost by release" }));
    await userEvent.click(table.getByRole("button", { name: "Actions for v1.4.0" }));
    const menu = within(await screen.findByRole("menu"));
    expect(menu.getByRole("menuitem", { name: "Open traces" })).toBeInTheDocument();
    expect(menu.queryByRole("menuitem", { name: "Set a budget for this" })).toBeNull();
  });

  it("links unpriced calls to Pricing and keeps the share bar", async () => {
    mockAttribution(() =>
      Promise.resolve(
        response({
          group_by: "trace_name",
          key: null,
          items: [row("support-bot", { unpriced_calls: 118 }), row("kb-search")],
        }),
      ),
    );
    renderApp(TRACE_NAMES);

    const table = within(await screen.findByRole("table", { name: "Cost by trace name" }));
    const links = table.getAllByRole("link", { name: "Set prices" });
    expect(links).toHaveLength(1);
    expect(new URL(links[0]?.getAttribute("href") ?? "", "http://localhost").pathname).toBe(
      "/acme/support/pricing",
    );
    const unpriced = links[0]?.closest("[role='row']") as HTMLElement;
    expect(within(unpriced).getByText("118")).toBeInTheDocument();
    expect(within(unpriced).getByText("61.2%")).toBeInTheDocument();
  });

  it("dims the previous bars until the focused group's series arrives", async () => {
    mockAttribution(() => Promise.resolve(traceNames()));
    vi.mocked(attributionApi.series).mockImplementation((_, query) =>
      query.value === undefined ? Promise.resolve(series()) : new Promise(() => undefined),
    );
    renderApp(TRACE_NAMES);

    const chart = await screen.findByRole("region", { name: "Spend over time" });
    await waitFor(() => {
      expect(chart.querySelector("[data-stale]")).toBeNull();
      expect(within(chart).getByRole("button", { name: "Show table" })).toBeInTheDocument();
    });
    const menu = await openRowMenu("support-bot");
    await userEvent.click(menu.getByRole("menuitem", { name: "Show over time" }));

    await waitFor(() => {
      expect(chart.querySelector("[data-stale]")).toHaveClass("opacity-60");
    });
    expect(chart).toHaveAttribute("aria-busy", "true");
  });

  it("gives each phone tile of a value its menu, and none or other tiles none", async () => {
    mockAttribution(() =>
      Promise.resolve(
        response({ none: bucket(), other: bucket({ folded_values: 3 }), items: [row("acme")] }),
      ),
    );
    renderApp(`${PAGE}?group_by=metadata&key=customer_id&range=30d`);

    const tiles = within(await screen.findByRole("list", { name: "Cost by customer_id" }));
    const trigger = tiles.getByRole("button", { name: "Actions for acme" });
    expect(trigger).toHaveClass("size-11");
    expect(trigger.closest("a")).toBeNull();
    const outside = within(screen.getByRole("region", { name: "Outside the list" }));
    expect(outside.queryByRole("button", { name: /^Actions for/ })).toBeNull();
  });
});
