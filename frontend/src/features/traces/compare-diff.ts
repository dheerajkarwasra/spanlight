import { lineDiff, type DiffLine } from "@/lib/text-diff";

import {
  messageText,
  parseChatPayload,
  payloadToClipboardText,
  ROLE_LABELS,
} from "./chat-messages";
import { guardPayload, PREVIEW_BYTES } from "./payload-guard";

/** Unchanged lines kept either side of a change. */
const CONTEXT = 3;

export type DiffItem = DiffLine | { kind: "gap"; count: number };

/**
 * A step's input or output as diffable text: chat messages as "Role:" and their text (the
 * Messages tab's reading), a string as it is, anything else as pretty JSON; "" when not captured.
 */
export function stepText(payload: unknown): string {
  if (payload === null || payload === undefined) {
    return "";
  }
  const messages = parseChatPayload(payload);
  if (messages === null) {
    return payloadToClipboardText(payload);
  }
  return messages
    .map(
      (message) =>
        `${ROLE_LABELS[message.role]}:\n${messageText(message) ?? JSON.stringify(message.parts, null, 2)}`,
    )
    .join("\n\n");
}

/**
 * Folds each run of unchanged lines down to the 3 lines next to a change; a text with no change
 * keeps its first 3 lines.
 */
export function collapseUnchanged(lines: readonly DiffLine[]): DiffItem[] {
  const items: DiffItem[] = [];
  let start = 0;
  while (start < lines.length) {
    if (lines[start]?.kind !== "same") {
      items.push(lines[start] as DiffLine);
      start += 1;
      continue;
    }
    let end = start;
    while (lines[end]?.kind === "same") {
      end += 1;
    }
    const run = lines.slice(start, end);
    const changeBefore = start > 0;
    const changeAfter = end < lines.length;
    const head = changeBefore || !changeAfter ? CONTEXT : 0;
    const tail = changeAfter ? CONTEXT : 0;
    if (run.length <= head + tail) {
      items.push(...run);
    } else {
      items.push(...run.slice(0, head), { kind: "gap", count: run.length - head - tail });
      items.push(...run.slice(run.length - tail));
    }
    start = end;
  }
  return items;
}

/** The first `PREVIEW_BYTES` of a text, ending at its last whole line when it has one. */
function preview(text: string): string {
  const bytes = new TextEncoder().encode(text);
  if (bytes.length <= PREVIEW_BYTES) {
    return text;
  }
  // `stream: true` holds back a multi-byte character cut by the slice instead of emitting U+FFFD.
  const shown = new TextDecoder().decode(bytes.subarray(0, PREVIEW_BYTES), { stream: true });
  const lastBreak = shown.lastIndexOf("\n");
  // The cut lands mid-line at a different point on each side; a partial line would read as changed.
  return lastBreak === -1 ? shown : shown.slice(0, lastBreak + 1);
}

/**
 * The folded diff of one field. When either side is over the payload guard (64 KB), both are
 * diffed on the same first 16 KB (whole lines only), which bounds the DOM text and the diff's
 * work; `cut` says so.
 */
export function stepDiff(before: string, after: string): { items: DiffItem[]; cut: boolean } {
  const cut = guardPayload(before).truncated || guardPayload(after).truncated;
  return {
    items: collapseUnchanged(
      cut ? lineDiff(preview(before), preview(after)) : lineDiff(before, after),
    ),
    cut,
  };
}
