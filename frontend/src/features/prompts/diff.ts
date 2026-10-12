import { diffLines } from "diff";

import type { ChatMessage, ChatRole, ModelAdvice, PromptTemplate } from "@/lib/api";

export type LineKind = "same" | "added" | "removed";

/** One line of a diff. Line numbers are 1-based; the side a line is missing from has null. */
export interface DiffLine {
  kind: LineKind;
  text: string;
  oldNumber: number | null;
  newNumber: number | null;
}

export interface DiffCounts {
  added: number;
  removed: number;
}

export type MessageStatus = "edited" | "unchanged" | "added" | "removed";

/** One chat message compared with the message at the same position in the other version. */
export interface MessageDiff {
  /** 0-based position in the chat. */
  index: number;
  status: MessageStatus;
  /** The role in the newer version (the older one for a removed message). */
  role: ChatRole;
  /** The older role when the role itself changed; otherwise null. */
  previousRole: ChatRole | null;
  lines: DiffLine[];
}

export type TemplateDiff =
  | { kind: "text"; lines: DiffLine[]; oldLineCount: number }
  | { kind: "chat"; messages: MessageDiff[] }
  /** The two versions are of different kinds; a prompt never changes kind, so this is defensive. */
  | { kind: "mixed" };

export interface ConfigChange {
  key: keyof ModelAdvice;
  before: string | number | null;
  after: string | number | null;
}

const CONFIG_KEYS: readonly (keyof ModelAdvice)[] = ["model", "temperature", "max_tokens", "top_p"];

function splitLines(value: string): string[] {
  const lines = value.split("\n");
  // `diffLines` keeps each line's newline, so a chunk ends with one empty piece after it.
  if (value.endsWith("\n")) {
    lines.pop();
  }
  return lines;
}

/**
 * A line diff of two texts. A changed line is one removal followed by one addition, never an
 * in-place edit, so the reader sees both versions of it whole.
 */
export function lineDiff(before: string, after: string): DiffLine[] {
  const result: DiffLine[] = [];
  let oldNumber = 0;
  let newNumber = 0;
  for (const change of diffLines(before, after)) {
    for (const text of splitLines(change.value)) {
      if (change.added) {
        newNumber += 1;
        result.push({ kind: "added", text, oldNumber: null, newNumber });
      } else if (change.removed) {
        oldNumber += 1;
        result.push({ kind: "removed", text, oldNumber, newNumber: null });
      } else {
        oldNumber += 1;
        newNumber += 1;
        result.push({ kind: "same", text, oldNumber, newNumber });
      }
    }
  }
  return result;
}

export function countChanges(lines: readonly DiffLine[]): DiffCounts {
  let added = 0;
  let removed = 0;
  for (const line of lines) {
    if (line.kind === "added") {
      added += 1;
    } else if (line.kind === "removed") {
      removed += 1;
    }
  }
  return { added, removed };
}

function messageDiff(index: number, before?: ChatMessage, after?: ChatMessage): MessageDiff {
  if (before === undefined && after !== undefined) {
    const lines = lineDiff("", after.content);
    return { index, status: "added", role: after.role, previousRole: null, lines };
  }
  if (after === undefined && before !== undefined) {
    const lines = lineDiff(before.content, "");
    return { index, status: "removed", role: before.role, previousRole: null, lines };
  }
  const older = before as ChatMessage;
  const newer = after as ChatMessage;
  const unchanged = older.role === newer.role && older.content === newer.content;
  return {
    index,
    status: unchanged ? "unchanged" : "edited",
    role: newer.role,
    previousRole: older.role === newer.role ? null : older.role,
    lines: lineDiff(older.content, newer.content),
  };
}

/** Chat templates are compared message by message, by position. */
export function chatDiff(
  before: readonly ChatMessage[],
  after: readonly ChatMessage[],
): MessageDiff[] {
  const length = Math.max(before.length, after.length);
  return Array.from({ length }, (_, index) => messageDiff(index, before[index], after[index]));
}

export function templateDiff(before: PromptTemplate, after: PromptTemplate): TemplateDiff {
  if (before.kind === "text" && after.kind === "text") {
    return {
      kind: "text",
      lines: lineDiff(before.text, after.text),
      oldLineCount: splitLines(before.text).length,
    };
  }
  if (before.kind === "chat" && after.kind === "chat") {
    return { kind: "chat", messages: chatDiff(before.messages, after.messages) };
  }
  return { kind: "mixed" };
}

/** Added and removed lines over the whole template. */
export function templateCounts(diff: TemplateDiff): DiffCounts {
  if (diff.kind === "text") {
    return countChanges(diff.lines);
  }
  if (diff.kind === "chat") {
    return countChanges(diff.messages.flatMap((message) => message.lines));
  }
  return { added: 0, removed: 0 };
}

/** Model-config fields whose value differs between two versions (unset reads as null). */
export function configChanges(before: ModelAdvice, after: ModelAdvice): ConfigChange[] {
  return CONFIG_KEYS.flatMap((key) => {
    const old = before[key] ?? null;
    const next = after[key] ?? null;
    return old === next ? [] : [{ key, before: old, after: next }];
  });
}

/** How many chat messages have each status, for the diff card's subtitle. */
export function statusCounts(messages: readonly MessageDiff[]): Record<MessageStatus, number> {
  const counts: Record<MessageStatus, number> = { edited: 0, unchanged: 0, added: 0, removed: 0 };
  for (const message of messages) {
    counts[message.status] += 1;
  }
  return counts;
}
