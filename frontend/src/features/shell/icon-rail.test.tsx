import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { insightsApi } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

vi.mock("@/features/alerts/alerts-page", () => ({
  AlertsPage: () => <p>Alerts page</p>,
}));

// The test setup's `matchMedia` matches nothing, so the desktop aside is the 1024–1279 icon rail.
describe("IconRail", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(insightsApi, "summary").mockResolvedValue({
      open_critical: 2,
      open_warning: 0,
      open_info: 0,
    });
  });

  it("names each hub link by its label, in rail order, and marks Monitor current on Alerts", async () => {
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    await screen.findByText("Alerts page");

    const nav = screen.getByRole("navigation", { name: "Main" });
    const monitor = await within(nav).findByRole("link", {
      name: "Monitor, 2 open critical findings",
    });
    const names = within(nav)
      .getAllByRole("link")
      .map((link) => link.getAttribute("aria-label"));
    expect(names).toEqual([
      "Overview",
      "Traces",
      "Monitor, 2 open critical findings",
      "Evaluate",
      "Prompts",
      "Cost",
      "Gateway",
      "Settings",
    ]);
    expect(monitor).toHaveAttribute("aria-current", "page");
    expect(
      within(nav)
        .getAllByRole("link")
        .filter((link) => link.hasAttribute("aria-current")),
    ).toHaveLength(1);
  });

  it("opens the full rail in the navigation sheet from Expand sidebar", async () => {
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    await screen.findByText("Alerts page");

    const expand = screen.getByRole("button", { name: "Expand sidebar" });
    expect(expand).toHaveAttribute("aria-haspopup", "dialog");
    await userEvent.click(expand);

    const sheet = await screen.findByRole("dialog", { name: "Navigation" });
    expect(within(sheet).getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });
});
