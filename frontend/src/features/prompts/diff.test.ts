import { describe, expect, it } from "vitest";

import { chatDiff, configChanges, statusCounts, templateCounts, templateDiff } from "./diff";

describe("chatDiff", () => {
  it("compares messages by position and flags role changes", () => {
    const messages = chatDiff(
      [
        { role: "system", content: "Be brief." },
        { role: "user", content: "{{q}}" },
      ],
      [
        { role: "system", content: "Be brief." },
        { role: "assistant", content: "{{q}}" },
        { role: "user", content: "More?" },
      ],
    );
    expect(messages.map((message) => message.status)).toEqual(["unchanged", "edited", "added"]);
    expect(messages[1]).toMatchObject({ role: "assistant", previousRole: "user" });
    expect(statusCounts(messages)).toEqual({ edited: 1, unchanged: 1, added: 1, removed: 0 });
  });

  it("keeps a removed message's role and counts its lines as removed", () => {
    const messages = chatDiff(
      [
        { role: "system", content: "x" },
        { role: "user", content: "line 1\nline 2" },
      ],
      [{ role: "system", content: "x" }],
    );
    expect(messages[1]).toMatchObject({ status: "removed", role: "user" });
    expect(templateCounts({ kind: "chat", messages })).toEqual({ added: 0, removed: 2 });
  });
});

describe("templateDiff", () => {
  it("refuses to line-diff templates of different kinds", () => {
    const diff = templateDiff(
      { kind: "text", text: "hi" },
      { kind: "chat", messages: [{ role: "user", content: "hi" }] },
    );
    expect(diff).toEqual({ kind: "mixed" });
    expect(templateCounts(diff)).toEqual({ added: 0, removed: 0 });
  });
});

describe("configChanges", () => {
  it("lists only fields that differ, an unset field reading as null", () => {
    expect(
      configChanges({ model: "gpt-4.1", temperature: 0.2 }, { model: "gpt-4.1", max_tokens: 512 }),
    ).toEqual([
      { key: "temperature", before: 0.2, after: null },
      { key: "max_tokens", before: null, after: 512 },
    ]);
  });
});
