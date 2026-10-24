import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { GatewayMode } from "@/lib/api";
import { ME } from "@/test/render-app";

import { GatewayBaseUrl } from "./gateway-base-url";

let mode: GatewayMode = "embedded";

vi.mock("@/features/auth", () => ({
  useMe: () => ({ ...ME, capabilities: { ...ME.capabilities, gateway_mode: mode } }),
}));

function renderPill(next: GatewayMode) {
  mode = next;
  render(<GatewayBaseUrl />);
}

describe("GatewayBaseUrl", () => {
  it("shows this origin's gateway URL with Copy when the gateway is embedded", () => {
    renderPill("embedded");
    expect(screen.getByText(`${window.location.origin}/gw/v1`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Copy/ })).toBeInTheDocument();
  });

  it("says a standalone gateway has its own URL, with nothing to copy", () => {
    renderPill("standalone");
    expect(screen.getByText("Standalone gateway · use its own URL")).toBeInTheDocument();
    expect(screen.queryByText(/\/gw\/v1/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Copy/ })).not.toBeInTheDocument();
  });

  it("says the gateway is off and names the setting that turns it on", () => {
    renderPill("disabled");
    expect(screen.getByText(/Gateway off on this server · set/)).toHaveTextContent(
      "Gateway off on this server · set GATEWAY_MODE",
    );
    expect(screen.getByText("GATEWAY_MODE").tagName).toBe("CODE");
    expect(screen.queryByRole("button", { name: /Copy/ })).not.toBeInTheDocument();
  });
});
