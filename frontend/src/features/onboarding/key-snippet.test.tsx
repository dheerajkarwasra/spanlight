import type * as Router from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, projectsApi, type ApiKey } from "@/lib/api";

import { KeySnippet } from "./key-snippet";

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof Router>()),
  Link: ({ children, search }: { children: ReactNode; search: Record<string, string> }) => (
    <a href={`/onboarding?org=${search.org}&project=${search.project}`}>{children}</a>
  ),
}));

let isDemo = false;

vi.mock("@/features/shell", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
  useCurrentOrg: () => ({ id: "org_1", is_demo: isDemo }),
}));

function key(overrides: Partial<ApiKey>): ApiKey {
  return {
    id: "k",
    name: "ci",
    prefix: "spl_live_AAAA",
    scopes: [],
    created_by: null,
    created_at: "2026-10-01T00:00:00Z",
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    ...overrides,
  };
}

function renderSnippet() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <KeySnippet code={(keyLine) => `${keyLine}  # comment`} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  isDemo = false;
});

describe("KeySnippet", () => {
  it("shows the newest active key's prefix and ignores revoked and expired keys", async () => {
    vi.spyOn(projectsApi, "keys").mockResolvedValue([
      key({ id: "old", prefix: "spl_live_OLD1", created_at: "2026-10-01T00:00:00Z" }),
      key({ id: "new", prefix: "NEW2", created_at: "2026-10-05T00:00:00Z" }),
      key({
        id: "revoked",
        prefix: "spl_live_REV3",
        created_at: "2026-10-09T00:00:00Z",
        revoked_at: "2026-10-10T00:00:00Z",
      }),
      key({
        id: "expired",
        prefix: "spl_live_EXP4",
        created_at: "2026-10-08T00:00:00Z",
        expires_at: "2026-10-09T00:00:00Z",
      }),
    ]);
    renderSnippet();
    expect(
      await screen.findByText("SPANLIGHT_API_KEY=spl_live_NEW2… # comment"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("links to onboarding when the project has no active key", async () => {
    vi.spyOn(projectsApi, "keys").mockResolvedValue([key({ revoked_at: "2026-10-10T00:00:00Z" })]);
    renderSnippet();
    const link = await screen.findByRole("link", { name: "Create an API key" });
    expect(link).toHaveAttribute("href", "/onboarding?org=org_1&project=proj_1");
    expect(screen.queryByText(/SPANLIGHT_API_KEY/)).not.toBeInTheDocument();
  });

  // Code review: a viewer can't list keys (403) and can't create one either, so no link.
  it("renders nothing when the viewer can't list keys", async () => {
    vi.spyOn(projectsApi, "keys").mockRejectedValue(new ApiError(403, {}));
    renderSnippet();
    await vi.waitFor(() => expect(projectsApi.keys).toHaveBeenCalled());
    await new Promise((done) => setTimeout(done, 50));
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText(/SPANLIGHT_API_KEY/)).not.toBeInTheDocument();
  });

  // Final review L1/L2: a long key line wraps inside the card instead of scrolling sideways.
  it("wraps a long key line", async () => {
    vi.spyOn(projectsApi, "keys").mockResolvedValue([key({})]);
    renderSnippet();
    const snippet = await screen.findByLabelText("Your API key setting");
    expect(snippet).toHaveClass("whitespace-pre-wrap", "[overflow-wrap:anywhere]");
    expect(snippet).not.toHaveClass("overflow-x-auto");
  });

  // Final review T3: the demo visitor can't create keys, so there is nothing to show.
  it("renders nothing in the demo org", async () => {
    isDemo = true;
    vi.spyOn(projectsApi, "keys").mockResolvedValue([key({})]);
    renderSnippet();
    await new Promise((done) => setTimeout(done, 50));
    expect(screen.queryByText(/SPANLIGHT_API_KEY/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    // Code review: and it doesn't ask for the keys at all.
    expect(projectsApi.keys).not.toHaveBeenCalled();
  });
});
