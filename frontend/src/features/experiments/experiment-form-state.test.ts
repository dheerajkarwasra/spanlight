import { describe, expect, it } from "vitest";

import type { PromptVersion } from "@/lib/api";

import { prefillState } from "./experiment-form-state";
import { newExperimentSearchSchema } from "./search";

const DATASET = "0b6f5d2e-8c1a-4e3b-9f7d-1a2b3c4d5e6f";
const VERSION_ID = "6a1e2f3b-4c5d-4e6f-8a7b-9c0d1e2f3a4b";

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

describe("newExperimentSearchSchema", () => {
  it("keeps uuids and a prompt name, drops ids that are not uuids", () => {
    expect(
      newExperimentSearchSchema.parse({
        dataset: DATASET,
        prompt: "support/answer",
        version: VERSION_ID,
      }),
    ).toEqual({ dataset: DATASET, prompt: "support/answer", version: VERSION_ID });
    expect(newExperimentSearchSchema.parse({ dataset: "ds_1", version: "3", prompt: "" })).toEqual(
      {},
    );
  });
});

describe("prefillState", () => {
  it("fills the dataset, prompt, version and the version's model", () => {
    expect(
      prefillState({ dataset: DATASET, prompt: "support/answer", version: VERSION_ID }, [VERSION]),
    ).toEqual({
      datasetId: DATASET,
      promptName: "support/answer",
      versionId: VERSION_ID,
      source: "prompt_version",
      model: "claude-haiku-4-5",
    });
  });

  it("leaves the version and model unset when the version is not among the prompt's", () => {
    const other = "11111111-2222-4333-8444-555555555555";
    expect(prefillState({ prompt: "support/answer", version: other }, [VERSION])).toEqual({
      promptName: "support/answer",
    });
    expect(prefillState({ prompt: "support/answer", version: VERSION_ID }, undefined)).toEqual({
      promptName: "support/answer",
    });
    // A version without its prompt can't be looked up.
    expect(prefillState({ version: VERSION_ID }, [VERSION])).toEqual({});
  });

  it("keeps the model blank when the version advises none", () => {
    const patch = prefillState({ prompt: "p", version: VERSION_ID }, [
      { ...VERSION, model_config: {} },
    ]);
    expect(patch).toEqual({ promptName: "p", versionId: VERSION_ID, source: "prompt_version" });
  });
});
