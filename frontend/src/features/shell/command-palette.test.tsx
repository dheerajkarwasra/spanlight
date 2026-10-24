import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { authApi } from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

vi.mock("@/features/gateway/credentials/credentials-page", () => ({
  GatewayCredentialsPage: () => <p>Credentials page</p>,
}));
vi.mock("@/features/billing-sources/reconciliation-tab-page", () => ({
  ReconciliationTabPage: () => <p>Reconciliation tab page</p>,
}));

async function openPalette() {
  const router = renderApp(`/${ORG_ID}/proj_1/settings/project`);
  await screen.findByRole("navigation", { name: "Main" });
  await userEvent.keyboard("{Control>}k{/Control}");
  const palette = await screen.findByRole("dialog", { name: "Command palette" });
  return { router, palette };
}

async function search(palette: HTMLElement, text: string) {
  const input = within(palette).getByRole("combobox");
  await userEvent.clear(input);
  await userEvent.type(input, text);
}

describe("CommandPalette", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
  });

  it.each([
    ["check bill", /^Reconciliation/],
    ["why is this expensive", /^Spend/],
    ["set prices", /^Pricing/],
    ["compare releases", /^Releases/],
    ["alert channels", /^Notification channels/],
  ])("finds a page by the task: %s", async (text, name) => {
    const { palette } = await openPalette();

    await search(palette, text);

    expect(within(palette).getByRole("option", { name })).toBeInTheDocument();
  });

  it("lists only the settings pages a viewer can read", async () => {
    const membership = ME.memberships[0];
    if (membership === undefined) {
      throw new Error("ME has no membership");
    }
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      memberships: [{ ...membership, role: "viewer" }],
    });
    const { palette } = await openPalette();

    const settings = within(palette).getByRole("group", { name: "Settings" });
    expect(within(settings).getByRole("option", { name: "Notification channels" })).toBeVisible();
    expect(within(settings).queryByRole("option", { name: "Audit log" })).not.toBeInTheDocument();
  });

  it("opens a hub tab without a G-hotkey", async () => {
    const { router, palette } = await openPalette();

    await search(palette, "credentials");
    await userEvent.click(within(palette).getByRole("option", { name: /^Credentials/ }));

    await screen.findByText("Credentials page");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/acme/checkout/gateway/credentials");
    });
  });

  it("opens Reconciliation in the Cost hub", async () => {
    const { router, palette } = await openPalette();

    await search(palette, "check bill");
    await userEvent.click(within(palette).getByRole("option", { name: /^Reconciliation/ }));

    await screen.findByText("Reconciliation tab page");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/acme/checkout/reconciliation");
    });
  });
});
