import { describe, expect, it } from "vitest";

import type { PromptVersion } from "@/lib/api";
import { TEMPLATE_TOO_LARGE_MESSAGE } from "@/lib/prompt-template";

import {
  MAX_TOKENS_RULE,
  TEMPERATURE_RULE,
  addColumn,
  columnProblem,
  duplicateColumn,
  effectiveCredentialId,
  firstProblem,
  newColumn,
  parseParam,
  pickVersion,
  removeColumn,
  toParams,
  toRequestConfig,
  updateColumn,
  type ColumnState,
} from "./config-state";

function version(overrides: Partial<PromptVersion>): PromptVersion {
  return {
    id: "pv_2",
    version: 2,
    kind: "text",
    template: { kind: "text", text: "Answer {{question}} for {{user}}" },
    variables: ["question", "user"],
    model_config: { model: "claude-haiku-4-5" },
    commit_message: null,
    created_by: null,
    created_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function ready(overrides: Partial<ColumnState> = {}): ColumnState {
  const column = newColumn("a");
  return {
    ...column,
    ...pickVersion(column, "support", version({})),
    variables: { question: "why?", user: "Ada" },
    ...overrides,
  };
}

describe("parseParam", () => {
  it("leaves an empty field to the provider (undefined), never 0", () => {
    expect(parseParam("  ", TEMPERATURE_RULE)).toBeUndefined();
  });

  it("refuses out-of-range and non-integer values with null", () => {
    expect(parseParam("2.5", TEMPERATURE_RULE)).toBeNull();
    expect(parseParam("abc", TEMPERATURE_RULE)).toBeNull();
    expect(parseParam("0", MAX_TOKENS_RULE)).toBeNull();
    expect(parseParam("1.5", MAX_TOKENS_RULE)).toBeNull();
    expect(parseParam("4097", MAX_TOKENS_RULE)).toBeNull();
    expect(parseParam("4096", MAX_TOKENS_RULE)).toBe(4096);
  });
});

describe("toParams", () => {
  it("sends only the parameters the user set", () => {
    expect(toParams(ready())).toEqual({});
    expect(toParams(ready({ temperature: "0", topP: "" }))).toEqual({ temperature: 0 });
  });
});

describe("pickVersion", () => {
  it("keeps typed values for variables the new version still uses and drops the rest", () => {
    const column = { ...newColumn("a"), variables: { question: "kept", old: "dropped" } };
    const patch = pickVersion(column, "support", version({}));
    expect(patch.variables).toEqual({ question: "kept", user: "" });
  });

  it("uses the version's model only when the column has none", () => {
    expect(pickVersion(newColumn("a"), "support", version({})).model).toBe("claude-haiku-4-5");
    const typed = { ...newColumn("a"), model: "gpt-4.1" };
    expect(pickVersion(typed, "support", version({})).model).toBe("gpt-4.1");
  });
});

describe("columns", () => {
  it("allow at most three and never remove the last one", () => {
    let columns = [newColumn("a")];
    columns = addColumn(addColumn(addColumn(columns, "b"), "c"), "d");
    expect(columns.map((column) => column.key)).toEqual(["a", "b", "c"]);
    expect(removeColumn([newColumn("a")], "a")).toHaveLength(1);
  });

  it("duplicate a column right after it without sharing its messages or variables", () => {
    const columns = duplicateColumn([ready(), newColumn("b")], "a", "copy");
    expect(columns.map((column) => column.key)).toEqual(["a", "copy", "b"]);
    const [original, copy] = columns;
    copy?.messages.push({ role: "user", content: "only in copy" });
    if (copy) copy.variables.question = "changed";
    expect(original?.messages).toHaveLength(1);
    expect(original?.variables.question).toBe("why?");
  });

  it("update only the named column", () => {
    const columns = updateColumn([newColumn("a"), newColumn("b")], "b", { model: "m" });
    expect(columns.map((column) => column.model)).toEqual(["", "m"]);
  });
});

describe("effectiveCredentialId", () => {
  it("uses the only credential when none is picked, and never guesses among several", () => {
    expect(effectiveCredentialId(newColumn("a"), ["c1"])).toBe("c1");
    expect(effectiveCredentialId(newColumn("a"), ["c1", "c2"])).toBe("");
    expect(effectiveCredentialId({ ...newColumn("a"), credentialId: "c2" }, ["c1"])).toBe("c2");
  });
});

describe("columnProblem", () => {
  it("names what stops a run, in the order the user fixes it", () => {
    expect(columnProblem(newColumn("a"), "c1")).toBe("Pick a prompt version.");
    expect(columnProblem({ ...newColumn("a"), sourceKind: "messages" }, "c1")).toBe(
      "Write at least one message.",
    );
    expect(columnProblem(ready(), "")).toBe("Choose a credential.");
    expect(columnProblem(ready({ model: "  " }), "c1")).toBe("Enter a model.");
    expect(columnProblem(ready({ temperature: "3", maxTokens: "0" }), "c1")).toBe(
      "Check Temperature (0 to 2), Max tokens (1 to 4096).",
    );
    expect(columnProblem(ready(), "c1")).toBeNull();
  });

  it("allows an empty variable value but not a missing one", () => {
    expect(columnProblem(ready({ variables: { question: "", user: "" } }), "c1")).toBeNull();
    expect(columnProblem(ready({ variables: { question: "x" } }), "c1")).toBe("Fill in user.");
  });

  it("stops a prompt that would pass the server's 256 KB cap once filled in", () => {
    const big = "x".repeat(200 * 1024);
    expect(columnProblem(ready({ variables: { question: big, user: big } }), "c1")).toBe(
      TEMPLATE_TOO_LARGE_MESSAGE,
    );
  });

  it("reports the first failing column by index", () => {
    expect(firstProblem([ready({ credentialId: "c1" }), newColumn("b")], ["c1", "c2"])).toEqual({
      index: 1,
      message: "Pick a prompt version.",
    });
  });
});

describe("toRequestConfig", () => {
  it("sends the version and its variables, or only the messages with text", () => {
    expect(toRequestConfig(ready({ model: " m " }), "c1")).toEqual({
      credential_id: "c1",
      model: "m",
      params: {},
      source: { prompt_version_id: "pv_2", variables: { question: "why?", user: "Ada" } },
    });
    const messages = ready({
      sourceKind: "messages",
      messages: [
        { role: "system", content: "  " },
        { role: "user", content: "hi" },
      ],
    });
    expect(toRequestConfig(messages, "c1").source).toEqual({
      messages: [{ role: "user", content: "hi" }],
    });
  });
});
