import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { authApi, promptsApi, type Prompt, type PromptVersion } from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PATH = `/${ORG_ID}/proj_1/prompts/answer_ticket`;

const VERSION: PromptVersion = {
  id: "ver_2",
  version: 2,
  kind: "text",
  template: { kind: "text", text: "Answer {{question}}" },
  variables: ["question"],
  model_config: {},
  commit_message: "Tighten refund wording",
  created_by: null,
  created_at: "2026-10-20T00:00:00Z",
};

const PROMPT: Prompt = {
  id: "prompt_1",
  name: "answer_ticket",
  description: null,
  kind: "text",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-20T00:00:00Z",
  latest: VERSION,
  labels: [{ label: "production", version: 2 }],
};

function mockPrompt() {
  vi.spyOn(promptsApi, "get").mockResolvedValue(PROMPT);
  vi.spyOn(promptsApi, "versions").mockResolvedValue({ items: [VERSION], next_cursor: null });
  vi.spyOn(promptsApi, "version").mockResolvedValue(VERSION);
  vi.spyOn(promptsApi, "versionUsage").mockResolvedValue({
    traces: 0,
    llm_calls: 0,
    cost_usd: null,
    p95_ms: null,
    has_unpriced: false,
  });
}

describe("Remove label", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    mockPrompt();
  });

  it("confirms the effect, then removes the label", async () => {
    const user = userEvent.setup();
    const deleteLabel = vi.spyOn(promptsApi, "deleteLabel").mockResolvedValue(undefined);
    renderApp(PATH);

    const versions = await screen.findByRole("list", { name: "Versions" });
    await user.click(within(versions).getByRole("button", { name: "Remove label production" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Remove label production?" });
    expect(dialog).toHaveTextContent(
      "Clients that fetch production will get an error until you set it again.",
    );
    // Focus starts on Cancel: removing is never the default.
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(deleteLabel).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Remove label" }));
    expect(deleteLabel).toHaveBeenCalledWith("proj_1", "answer_ticket", "production");
  });

  it("offers no remove control to a viewer", async () => {
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      memberships: ME.memberships.map((membership) => ({ ...membership, role: "viewer" })),
    });
    renderApp(PATH);

    const versions = await screen.findByRole("list", { name: "Versions" });
    expect(within(versions).getByText("production")).toBeInTheDocument();
    expect(
      within(versions).queryByRole("button", { name: /^Remove label/ }),
    ).not.toBeInTheDocument();
  });
});
