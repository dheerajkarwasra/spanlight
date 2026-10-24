import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { authApi, credentialsApi, gatewayApi } from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PATH = `/${ORG_ID}/proj_1/gateway/credentials`;

describe("GatewayCredentialsPage", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(credentialsApi, "list").mockResolvedValue([]);
    vi.spyOn(gatewayApi, "routes").mockResolvedValue([]);
  });

  it("says up front that the server key isn't set and disables Add, with no failed POST", async () => {
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      capabilities: { ...ME.capabilities, credentials_configured: false },
    });
    const create = vi.spyOn(credentialsApi, "create");
    renderApp(PATH);

    expect(await screen.findByText("Not configured on this server")).toBeInTheDocument();
    // The header and the empty state both offer Add.
    const buttons = screen.getAllByRole("button", { name: "Add credential" });
    for (const add of buttons) {
      expect(add).toHaveAttribute("aria-disabled", "true");
    }
    const [add] = buttons;
    expect(add).toHaveAccessibleDescription(/CREDENTIALS_KEYS/);

    await userEvent.click(add as HTMLElement);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("offers Add when the server key is set", async () => {
    renderApp(PATH);
    const [add] = await screen.findAllByRole("button", { name: "Add credential" });
    expect(add).not.toHaveAttribute("aria-disabled");
    expect(screen.queryByText("Not configured on this server")).not.toBeInTheDocument();
  });
});
