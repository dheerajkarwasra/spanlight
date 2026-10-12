import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ApiKey, User } from "@/lib/api";

import { ApiKeyList } from "./api-key-list";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const me: User = {
  id: "u1",
  email: "me@example.com",
  name: "Me",
  created_at: "",
  email_verified: true,
};
const colleague: User = {
  id: "u2",
  email: "priya@example.com",
  name: "Priya",
  created_at: "",
  email_verified: true,
};

function makeKey(overrides: Partial<ApiKey>): ApiKey {
  return {
    id: "k",
    name: "key",
    prefix: "ABCDEFGHIJKL",
    scopes: ["ingest:write"],
    created_by: me,
    created_at: "2026-10-01T00:00:00Z",
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    ...overrides,
  };
}

function renderList(keys: ApiKey[]) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TooltipProvider>
        <ApiKeyList
          keys={keys}
          ability={{ canRevokeAny: false, canRevokeOwn: true, userId: me.id }}
        />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

/** The text of every "Last used …" cell inside a row. */
function lastUsedTexts(row: HTMLElement): string[] {
  return Array.from(row.querySelectorAll("span"))
    .map((element) => element.textContent.replace(/\s+/g, " ").trim())
    .filter((text) => text.startsWith("Last used ") && text !== "Last used");
}

describe("ApiKeyList", () => {
  it("lets a member revoke only their own active keys", () => {
    renderList([
      makeKey({ id: "own", name: "mine" }),
      makeKey({ id: "other", name: "theirs", created_by: colleague }),
      makeKey({ id: "old", name: "retired", revoked_at: "2026-10-05T00:00:00Z" }),
    ]);

    const rows = within(screen.getByRole("list", { name: "API keys" })).getAllByRole("listitem");
    const [own, other, revoked] = rows;
    if (!own || !other || !revoked) {
      throw new Error("expected three rows");
    }

    expect(within(own).getByRole("button", { name: "Revoke key mine" })).toBeEnabled();

    expect(within(other).getByRole("button", { name: "Revoke" })).toBeDisabled();
    expect(within(other).getByText("You can only revoke keys you created.")).toBeInTheDocument();

    expect(within(revoked).queryByRole("button")).not.toBeInTheDocument();
    expect(within(revoked).getAllByText("Revoked").length).toBeGreaterThan(0);
  });

  it("says when a key has never been used", () => {
    renderList([makeKey({ name: "fresh", last_used_at: null })]);

    // The "Last used" column reads "Never" for a key nothing has called yet (both the wide-row
    // cell and the narrow-row summary line).
    const row = within(screen.getByRole("list", { name: "API keys" })).getByRole("listitem");
    expect(lastUsedTexts(row)).toEqual(["Last used Never", "Last used Never"]);
  });
});
