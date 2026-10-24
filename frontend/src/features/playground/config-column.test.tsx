import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  credentialsApi,
  pricesApi,
  promptsApi,
  type PromptListItem,
  type PromptVersion,
} from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

// Each test renders the whole app; under load the first one's cold lazy route chunk took over 5 s.
vi.setConfig({ testTimeout: 20_000 });

const PROMPT: PromptListItem = {
  id: "prompt_1",
  name: "support",
  description: null,
  kind: "chat",
  latest_version: 3,
  labels: [],
  updated_at: "2026-10-20T00:00:00Z",
};

const VERSION: PromptVersion = {
  id: "ver_3",
  version: 3,
  kind: "chat",
  template: { kind: "chat", messages: [{ role: "user", content: "Hi {{name}}" }] },
  variables: ["name"],
  model_config: {},
  commit_message: null,
  created_by: null,
  created_at: "2026-10-20T00:00:00Z",
};

beforeEach(() => {
  mockSignedIn([makeProject("proj_1", "Support")]);
  vi.spyOn(credentialsApi, "list").mockResolvedValue([]);
  vi.spyOn(pricesApi, "list").mockResolvedValue([]);
  vi.spyOn(promptsApi, "list").mockResolvedValue({ items: [PROMPT], next_cursor: null });
  vi.spyOn(promptsApi, "version").mockResolvedValue(VERSION);
});

describe("Playground column Create experiment", () => {
  it("shows for a picked version and links its prompt and version; not for messages", async () => {
    const user = userEvent.setup();
    renderApp(`/${ORG_ID}/proj_1/playground?prompt=support&v=3`);

    const link = await screen.findByRole(
      "link",
      { name: "Create experiment from run A" },
      { timeout: 15_000 },
    );
    expect(link).toHaveAttribute(
      "href",
      expect.stringMatching(/\/experiments\/new\?prompt=support&version=ver_3$/),
    );
    await user.hover(link);
    expect(await screen.findByRole("tooltip", { name: "Create experiment" })).toBeInTheDocument();

    const run = screen.getByRole("group", { name: "Run A" });
    await user.click(within(run).getByRole("radio", { name: "Messages" }));
    expect(screen.queryByRole("link", { name: "Create experiment from run A" })).toBeNull();
  });

  it("offers Create experiment in the Saved as note", async () => {
    vi.spyOn(promptsApi, "createVersion").mockResolvedValue({
      ...VERSION,
      id: "ver_4",
      version: 4,
    });
    const user = userEvent.setup();
    renderApp(`/${ORG_ID}/proj_1/playground`);

    const run = await screen.findByRole("group", { name: "Run A" }, { timeout: 15_000 });
    await user.click(within(run).getByRole("radio", { name: "Messages" }));
    await user.type(within(run).getByRole("textbox", { name: "Text of message 1" }), "Be brief");
    await user.click(within(run).getByRole("button", { name: "Save as version" }));
    await user.click(await screen.findByRole("button", { name: "Save version" }));

    const note = await within(run).findByRole("status");
    expect(note).toHaveTextContent("Saved as v4.");
    expect(within(note).getByRole("link", { name: "Create experiment" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/experiments\/new\?prompt=support&version=ver_4$/),
    );
  });

  // Ruling 20 (WCAG 2.5.8): the 16 px checkbox gets a 24 px target.
  it("gives the version-config checkbox a 24 px target", async () => {
    const user = userEvent.setup();
    renderApp(`/${ORG_ID}/proj_1/playground`);

    const run = await screen.findByRole("group", { name: "Run A" }, { timeout: 15_000 });
    await user.click(within(run).getByRole("radio", { name: "Messages" }));
    await user.click(within(run).getByRole("button", { name: "Save as version" }));
    const checkbox = await screen.findByRole("checkbox", {
      name: /Also save the model and parameters/,
    });
    expect(checkbox.parentElement?.tagName).toBe("LABEL");
    expect(checkbox.parentElement).toHaveClass("size-6");
  });
});
