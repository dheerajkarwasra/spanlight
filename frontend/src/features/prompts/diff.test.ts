import { describe, expect, it } from "vitest";

import {
  chatDiff,
  configChanges,
  countChanges,
  lineDiff,
  statusCounts,
  templateCounts,
  templateDiff,
} from "./diff";

describe("lineDiff", () => {
  it("shows a changed line as a removal then an addition, with numbers on each side", () => {
    expect(lineDiff("a\nb\nc", "a\nB\nc")).toEqual([
      { kind: "same", text: "a", oldNumber: 1, newNumber: 1 },
      { kind: "removed", text: "b", oldNumber: 2, newNumber: null },
      { kind: "added", text: "B", oldNumber: null, newNumber: 2 },
      { kind: "same", text: "c", oldNumber: 3, newNumber: 3 },
    ]);
  });

  it("keeps blank lines and counts every added and removed line", () => {
    const lines = lineDiff("one\n\nthree\n", "one\n\nthree\nfour\nfive\n");
    expect(lines.map((line) => line.text)).toEqual(["one", "", "three", "four", "five"]);
    expect(countChanges(lines)).toEqual({ added: 2, removed: 0 });
  });

  it("reports no change for identical texts", () => {
    expect(countChanges(lineDiff("same\ntext", "same\ntext"))).toEqual({ added: 0, removed: 0 });
  });
});

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
