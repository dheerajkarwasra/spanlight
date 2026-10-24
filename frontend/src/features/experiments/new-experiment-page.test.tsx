import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  credentialsApi,
  datasetsApi,
  evaluatorsApi,
  pricesApi,
  promptsApi,
  type Dataset,
  type PromptListItem,
  type PromptVersion,
} from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

// Each test renders the whole app; under load the first one's cold lazy route chunk took over 5 s.
vi.setConfig({ testTimeout: 20_000 });

const DATASET = "0b6f5d2e-8c1a-4e3b-9f7d-1a2b3c4d5e6f";
const VERSION_ID = "6a1e2f3b-4c5d-4e6f-8a7b-9c0d1e2f3a4b";
const PATH = `/${ORG_ID}/proj_1/experiments/new`;

const DATASET_ROW: Dataset = {
  id: DATASET,
  name: "support-questions",
  description: null,
  item_count: 50,
  locked_at: null,
  created_at: "2026-10-02T00:00:00Z",
  updated_at: "2026-10-02T00:00:00Z",
};

const PROMPT: PromptListItem = {
  id: "prompt_1",
  name: "support/answer",
  description: null,
  kind: "text",
  latest_version: 3,
  labels: [],
  updated_at: "2026-10-20T00:00:00Z",
};

const VERSION: PromptVersion = {
  id: VERSION_ID,
  version: 3,
  kind: "text",
  template: { kind: "text", text: "Answer {{question}}" },
  variables: ["question"],
  model_config: { model: "claude-haiku-4-5" },
  commit_message: null,
  created_by: null,
  created_at: "2026-10-20T00:00:00Z",
};

beforeEach(() => {
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  vi.spyOn(datasetsApi, "list").mockResolvedValue({ items: [DATASET_ROW], next_cursor: null });
  vi.spyOn(promptsApi, "list").mockResolvedValue({ items: [PROMPT], next_cursor: null });
  vi.spyOn(promptsApi, "versions").mockResolvedValue({ items: [VERSION], next_cursor: null });
  vi.spyOn(credentialsApi, "list").mockResolvedValue([]);
  vi.spyOn(evaluatorsApi, "list").mockResolvedValue({ items: [], next_cursor: null });
  vi.spyOn(pricesApi, "list").mockResolvedValue([]);
});

describe("New experiment prefilled from a link", () => {
  it("fills the dataset, prompt, version and model once; later edits stay", async () => {
    const versions = vi.mocked(promptsApi.versions);
    renderApp(
      `${PATH}?dataset=${DATASET}&prompt=${encodeURIComponent("support/answer")}&version=${VERSION_ID}`,
    );

    const model = await screen.findByDisplayValue("claude-haiku-4-5", {}, { timeout: 15_000 });
    expect(screen.getByRole("combobox", { name: "Dataset" })).toHaveTextContent(
      "support-questions · 50 items",
    );
    expect(screen.getByRole("combobox", { name: "Prompt" })).toHaveTextContent("support/answer");
    expect(screen.getByRole("combobox", { name: "Version" })).toHaveTextContent("v3");

    await userEvent.clear(model);
    await userEvent.type(model, "gpt-4.1-mini");
    await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "refunds");
    expect(model).toHaveValue("gpt-4.1-mini");
    // The prefill asked for the versions once: no request loop.
    expect(versions).toHaveBeenCalledTimes(1);
  });

  // Code review: a failed datasets load used to spend the prefill, so Try again left it unpicked.
  it("still picks the link's dataset after the datasets load fails and Try again succeeds", async () => {
    vi.spyOn(datasetsApi, "list")
      .mockRejectedValueOnce(new ApiError(403, {}))
      .mockResolvedValue({ items: [DATASET_ROW], next_cursor: null });
    const user = userEvent.setup();
    renderApp(`${PATH}?dataset=${DATASET}`);

    await user.click(await screen.findByRole("button", { name: "Try again" }, { timeout: 15_000 }));

    expect(await screen.findByRole("combobox", { name: "Dataset" })).toHaveTextContent(
      "support-questions · 50 items",
    );
  });

  it("leaves fields that don't resolve empty and the form usable", async () => {
    vi.spyOn(datasetsApi, "list").mockResolvedValue({ items: [], next_cursor: null });
    const other = "11111111-2222-4333-8444-555555555555";
    renderApp(`${PATH}?dataset=${DATASET}&prompt=support%2Fanswer&version=${other}`);

    const dataset = await screen.findByRole("combobox", { name: "Dataset" }, { timeout: 15_000 });
    expect(dataset).toHaveTextContent("Choose a dataset");
    expect(await screen.findByRole("combobox", { name: "Version" })).toHaveTextContent(
      "Choose a version",
    );
    expect(screen.getByRole("textbox", { name: "Model" })).toHaveValue("");
    await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "refunds");
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("refunds");
  });

  it("keeps what the user set while the prefill was still loading", async () => {
    let resolve: (page: { items: PromptVersion[]; next_cursor: null }) => void = () => undefined;
    vi.spyOn(promptsApi, "versions").mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const user = userEvent.setup();
    renderApp(`${PATH}?prompt=support%2Fanswer&version=${VERSION_ID}`);

    const model = await screen.findByRole("textbox", { name: "Model" }, { timeout: 15_000 });
    await user.type(model, "gpt-4.1-mini");
    await user.click(screen.getByRole("radio", { name: "Ad-hoc messages" }));
    resolve({ items: [VERSION], next_cursor: null });

    await vi.waitFor(() => expect(promptsApi.versions).toHaveBeenCalled());
    await new Promise((done) => setTimeout(done, 50));
    expect(model).toHaveValue("gpt-4.1-mini");
    expect(screen.getByRole("radio", { name: "Ad-hoc messages" })).toBeChecked();
    // Back on prompt versions, the prompt and version from the link are there.
    await user.click(screen.getByRole("radio", { name: "Prompt version" }));
    expect(await screen.findByRole("combobox", { name: "Version" })).toHaveTextContent("v3");
  });

  it("leaves the prompt empty when no prompt has that name", async () => {
    vi.spyOn(promptsApi, "versions").mockRejectedValue(new ApiError(404, {}));
    renderApp(`${PATH}?prompt=ghost&version=${VERSION_ID}`);

    const prompt = await screen.findByRole("combobox", { name: "Prompt" }, { timeout: 15_000 });
    await vi.waitFor(() => expect(prompt).toHaveTextContent("Choose a prompt"));
    expect(screen.getByRole("combobox", { name: "Version" })).toHaveTextContent(
      "Choose a prompt first",
    );
  });

  // Final review: the link's version, source and model belong to the link's prompt.
  it("applies nothing of the link's version once the user picked another prompt", async () => {
    vi.spyOn(promptsApi, "list").mockResolvedValue({
      items: [PROMPT, { ...PROMPT, id: "prompt_2", name: "billing/answer" }],
      next_cursor: null,
    });
    let resolve: (page: { items: PromptVersion[]; next_cursor: null }) => void = () => undefined;
    vi.spyOn(promptsApi, "versions").mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const user = userEvent.setup();
    renderApp(`${PATH}?prompt=support%2Fanswer&version=${VERSION_ID}`);

    const prompt = await screen.findByRole("combobox", { name: "Prompt" }, { timeout: 15_000 });
    await user.click(prompt);
    await user.click(await screen.findByRole("option", { name: "billing/answer" }));
    resolve({ items: [VERSION], next_cursor: null });

    await vi.waitFor(() => expect(promptsApi.versions).toHaveBeenCalled());
    await new Promise((done) => setTimeout(done, 50));
    expect(prompt).toHaveTextContent("billing/answer");
    expect(screen.getByRole("textbox", { name: "Model" })).toHaveValue("");
  });
});
