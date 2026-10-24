import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import type { CreatedGatewayKey, GatewayMode } from "@/lib/api";
import { ME } from "@/test/render-app";

import { KeySecretReveal } from "./key-secret-reveal";

let mode: GatewayMode = "embedded";

vi.mock("@/features/auth", () => ({
  useMe: () => ({ ...ME, capabilities: { ...ME.capabilities, gateway_mode: mode } }),
}));

const CREATED = {
  name: "checkout",
  secret: "spl_gw_secret",
  environment: "production",
  rpm_limit: null,
  tpm_limit: null,
  cache_ttl_seconds: null,
} as unknown as CreatedGatewayKey;

function renderReveal(next: GatewayMode) {
  mode = next;
  render(
    <Dialog open>
      <DialogContent>
        <KeySecretReveal created={CREATED} routeName={null} />
      </DialogContent>
    </Dialog>,
  );
}

describe("KeySecretReveal", () => {
  it.each([
    ["standalone", "This server runs the gateway standalone; use your gateway's own URL."],
    ["disabled", "The gateway is off on this server; set GATEWAY_MODE to turn it on."],
  ] as const)("says in a full sentence why the snippet has no URL (%s)", (next, sentence) => {
    renderReveal(next);
    expect(screen.getByText(/Point your SDK at the gateway/)).toHaveTextContent(
      `Point your SDK at the gateway with this key. ${sentence}`,
    );
  });
});
