import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { promptsApi, type Prompt, type PromptVersion } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const media = vi.hoisted(() => ({ phone: false }));

vi.mock("@/lib/use-media-query", () => ({
  useMediaQuery: (query: string) => media.phone && query === "(max-width: 639px)",
}));

const PATH = `/${ORG_ID}/proj_1/prompts/answer_ticket`;

const USAGE = { traces: 12, llm_calls: 14, cost_usd: "0.041", p95_ms: 820, has_unpriced: false };

const V2: PromptVersion = {
  id: "ver_2",
  version: 2,
  kind: "text",
  template: { kind: "text", text: "Answer {{question}}" },
  variables: ["question"],
  model_config: {},
  commit_message: "Tighten refund wording",
  created_by: null,
  created_at: "2026-10-20T00:00:00Z",
  usage_7d: USAGE,
};

const V1: PromptVersion = {
  ...V2,
  id: "ver_1",
  version: 1,
  commit_message: "First draft",
  usage_7d: { traces: 0, llm_calls: 0, cost_usd: null, p95_ms: null, has_unpriced: false },
};

const PROMPT: Prompt = {
  id: "prompt_1",
  name: "answer_ticket",
  description: null,
  kind: "text",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-20T00:00:00Z",
  latest: V2,
  labels: [{ label: "production", version: 2 }],
};

describe("Version rows", () => {
  beforeEach(() => {
    media.phone = false;
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(promptsApi, "get").mockResolvedValue(PROMPT);
    vi.spyOn(promptsApi, "versions").mockResolvedValue({ items: [V2, V1], next_cursor: null });
    vi.spyOn(promptsApi, "versionUsage").mockResolvedValue(USAGE);
  });

  it("asks for usage and shows labels right after the version number, then usage", async () => {
    renderApp(PATH);
    const versions = await screen.findByRole("list", { name: "Versions" });
    const [newest, oldest] = within(versions).getAllByRole("listitem");

    const text = newest?.textContent ?? "";
    const order = ["v2", "production", "latest", "Tighten refund wording", "12 traces"].map(
      (part) => text.indexOf(part),
    );
    expect(order.every((index) => index >= 0)).toBe(true);
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(oldest).toHaveTextContent("No calls in 7 days");
    expect(promptsApi.versions).toHaveBeenCalledWith(
      "proj_1",
      "answer_ticket",
      expect.objectContaining({ usage: true }),
    );
  });

  it("keeps the full header and the Usage card on a wide screen", async () => {
    renderApp(PATH);
    expect(await screen.findByRole("link", { name: "New version" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in playground" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Move label" })).not.toBeInTheDocument();
    expect(screen.getByText("Usage · v2")).toBeInTheDocument();
  });

  it("is a read-only version list below 640 px, with Move label reachable", async () => {
    media.phone = true;
    const user = userEvent.setup();
    renderApp(PATH);

    const move = await screen.findByRole("button", { name: "Move label" });
    expect(screen.getByRole("button", { name: "More actions for answer_ticket" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "New version" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open in playground" })).not.toBeInTheDocument();
    expect(screen.getByText("Edit and test versions on a larger screen.")).toBeInTheDocument();
    expect(screen.queryByText("Usage · v2")).not.toBeInTheDocument();
    expect(await screen.findByText("Template · v2")).toBeInTheDocument();

    await user.click(move);
    expect(await screen.findByRole("dialog", { name: "Set label" })).toBeInTheDocument();
  });

  it("starts a prefilled experiment from a version row and from the header", async () => {
    renderApp(PATH);
    const row = await screen.findByRole("link", { name: "Create experiment from v1" });
    expect(row).toHaveAttribute(
      "href",
      expect.stringMatching(/\/experiments\/new\?prompt=answer_ticket&version=ver_1$/),
    );
    expect(screen.getByRole("link", { name: "Create experiment" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/experiments\/new\?prompt=answer_ticket&version=ver_2$/),
    );
  });

  it("puts Create experiment in the phone row menu, not the phone header", async () => {
    media.phone = true;
    const user = userEvent.setup();
    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: "Actions for v2" }));
    expect(await screen.findByRole("menuitem", { name: "Create experiment" })).toHaveAttribute(
      "href",
      expect.stringMatching(/version=ver_2$/),
    );
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("link", { name: "Create experiment" })).not.toBeInTheDocument();
  });
});
