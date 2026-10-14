import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { Credential } from "@/lib/api";

import { CredentialRow } from "./credential-row";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const credential: Credential = {
  id: "c3b1f0a4-7d2e-4e8a-9b61-5f2d8a0c4e17",
  name: "anthropic-prod",
  provider: "anthropic",
  base_url: null,
  created_by: null,
  created_at: "2026-10-01T00:00:00Z",
  last_used_at: null,
  last_checked_at: null,
  last_error: null,
  rotated_at: null,
};

function renderRow() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TooltipProvider>
        <ul>
          <CredentialRow
            credential={credential}
            canManage
            ownerReasonId="owner-reason"
            uses={undefined}
            routes={[]}
            onNotConfigured={() => undefined}
          />
        </ul>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("CredentialRow", () => {
  it("shows the credential id with a copy button named after the credential", () => {
    renderRow();

    expect(screen.getByText(credential.id)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy anthropic-prod ID" })).toBeInTheDocument();
  });
});
