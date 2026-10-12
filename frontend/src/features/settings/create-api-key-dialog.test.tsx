import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { projectsApi, type CreatedApiKey } from "@/lib/api";

import { CreateApiKeyDialog } from "./create-api-key-dialog";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const SECRET = "spl_live_abcdefghijkl_abcdefghijklmnopqrstuvwxyz234567";

const createdKey: CreatedApiKey = {
  id: "key_1",
  name: "production-api",
  prefix: "spl_live_abcdefghijkl",
  scopes: ["ingest:write"],
  created_by: null,
  created_at: "2026-10-07T00:00:00Z",
  last_used_at: null,
  expires_at: null,
  revoked_at: null,
  secret: SECRET,
};

function renderDialog() {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <CreateApiKeyDialog trigger={<Button>Create key</Button>} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("CreateApiKeyDialog", () => {
  it("shows the secret once and forgets it when closed", async () => {
    const user = userEvent.setup();
    const createKey = vi.spyOn(projectsApi, "createKey").mockResolvedValue(createdKey);
    renderDialog();

    await user.click(screen.getByRole("button", { name: "Create key" }));
    await user.type(screen.getByLabelText("Name"), "  production-api ");
    await user.click(screen.getByRole("button", { name: "Create key" }));

    // The form sends the trimmed name, the default scope and the default 90-day expiry.
    expect(createKey).toHaveBeenCalledTimes(1);
    const [projectId, input] = createKey.mock.calls[0] ?? [];
    expect(projectId).toBe("proj_1");
    expect(input?.name).toBe("production-api");
    expect(input?.scopes).toEqual(["ingest:write"]);
    const expiresIn = new Date(input?.expires_at ?? "").getTime() - Date.now();
    expect(Math.abs(expiresIn - 90 * 24 * 3_600_000)).toBeLessThan(60_000);
    expect(await screen.findByText(SECRET)).toBeInTheDocument();
    expect(screen.getByText(`SPANLIGHT_API_KEY=${SECRET}`)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create key" }));
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("");
  });

  it("validates the name before calling the API", async () => {
    const user = userEvent.setup();
    const createKey = vi.spyOn(projectsApi, "createKey");
    renderDialog();

    await user.click(screen.getByRole("button", { name: "Create key" }));
    await user.click(screen.getByRole("button", { name: "Create key" }));

    expect(await screen.findByText("Enter a name for this key.")).toBeInTheDocument();
    expect(createKey).not.toHaveBeenCalled();
  });
});
