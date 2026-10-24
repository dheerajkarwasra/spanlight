import { diffLines } from "diff";

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

export function splitLines(value: string): string[] {
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
