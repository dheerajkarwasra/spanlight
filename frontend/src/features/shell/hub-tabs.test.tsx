import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { insightsApi } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

// The row is the shell's; the pages under it only need to render.
vi.mock("@/features/doctor/doctor-page", () => ({
  DoctorPage: () => <p>Doctor page</p>,
}));
vi.mock("@/features/gateway/keys/keys-page", () => ({
  GatewayKeysPage: () => <p>Keys page</p>,
}));
vi.mock("@/features/billing-sources/reconciliation-tab-page", () => ({
  ReconciliationTabPage: () => <p>Reconciliation tab page</p>,
}));

describe("HubTabs", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(insightsApi, "summary").mockResolvedValue({
      open_critical: 2,
      open_warning: 0,
      open_info: 0,
    });
  });

  it("lists the Monitor pages, marks Doctor current and carries its count inside the pill", async () => {
    renderApp(`/${ORG_ID}/proj_1/doctor`);
    await screen.findByText("Doctor page");

    const nav = screen.getByRole("navigation", { name: "Monitor sections" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      expect.stringContaining("Doctor"),
      "Provider health",
      "Releases",
      "Alerts",
    ]);
    const doctor = within(nav).getByRole("link", { name: /^Doctor/ });
    expect(doctor).toHaveAttribute("aria-current", "page");
    expect(links.filter((link) => link.hasAttribute("aria-current"))).toHaveLength(1);
    expect(
      await within(doctor).findByRole("img", { name: "2 critical insights need attention" }),
    ).toBeInTheDocument();

    expect(screen.getByRole("button", { name: /Section/ })).toHaveTextContent("Doctor");
    const docs = screen.getByRole("link", { name: /Docs.*opens in a new tab/ });
    expect(docs).toHaveAttribute("target", "_blank");
    expect(docs).toHaveAttribute("href", "/docs/doctor/");
  });

  it("marks the longest match current, so Gateway › Keys is not Overview", async () => {
    renderApp(`/${ORG_ID}/proj_1/gateway/keys`);
    await screen.findByText("Keys page");

    const nav = screen.getByRole("navigation", { name: "Gateway sections" });
    expect(within(nav).getByRole("link", { name: "Keys" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("button", { name: /Section/ })).toHaveTextContent("Keys");
  });

  it("keeps Reconciliation in the Cost row, with no Settings marker", async () => {
    renderApp(`/${ORG_ID}/proj_1/reconciliation`);
    await screen.findByText("Reconciliation tab page");

    const nav = screen.getByRole("navigation", { name: "Cost sections" });
    const tab = within(nav).getByRole("link", { name: "Reconciliation" });
    expect(tab).toHaveAttribute("aria-current", "page");
    expect(tab).toHaveAttribute("href", "/acme/checkout/reconciliation");
    expect(tab).not.toHaveAttribute("aria-description");
  });
});
