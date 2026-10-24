/** Pure layout of the compare output diff: A and B lines side by side, long unchanged runs folded. */
import type { DiffLine } from "@/lib/text-diff";

export interface AlignedRow {
  kind: "same" | "changed" | "collapsed";
  /** The line's text on each side; null where that side has no line on this row. */
  a: string | null;
  b: string | null;
  /** 1-based line numbers; absent on a collapsed row. */
  aNumber?: number | null;
  bNumber?: number | null;
  /** How many unchanged lines a collapsed row stands for. */
  count?: number;
}

function sameRow(line: DiffLine): AlignedRow {
  return {
    kind: "same",
    a: line.text,
    b: line.text,
    aNumber: line.oldNumber,
    bNumber: line.newNumber,
  };
}

/**
 * Rows of a two-column diff. Each block of removed then added lines is paired row by row (the
 * shorter side is padded with null), and an unchanged run longer than `context * 2` lines keeps
 * `context` lines on each end around one "N unchanged lines" row.
 */
export function alignDiff(lines: readonly DiffLine[], context = 3): AlignedRow[] {
  const rows: AlignedRow[] = [];
  let index = 0;
  while (index < lines.length) {
    const start = index;
    if (lines[index]?.kind === "same") {
      while (lines[index]?.kind === "same") {
        index += 1;
      }
      const run = lines.slice(start, index);
      if (run.length > context * 2) {
        rows.push(...run.slice(0, context).map(sameRow));
        rows.push({ kind: "collapsed", a: null, b: null, count: run.length - context * 2 });
        rows.push(...run.slice(-context).map(sameRow));
      } else {
        rows.push(...run.map(sameRow));
      }
      continue;
    }
    while (index < lines.length && lines[index]?.kind !== "same") {
      index += 1;
    }
    const removed = lines.slice(start, index).filter((line) => line.kind === "removed");
    const added = lines.slice(start, index).filter((line) => line.kind === "added");
    for (let row = 0; row < Math.max(removed.length, added.length); row += 1) {
      rows.push({
        kind: "changed",
        a: removed[row]?.text ?? null,
        b: added[row]?.text ?? null,
        aNumber: removed[row]?.oldNumber ?? null,
        bNumber: added[row]?.newNumber ?? null,
      });
    }
  }
  return rows;
}
