/**
 * A preview of a dataset upload, read in the browser before anything is sent: the first rows of a
 * `.jsonl` or `.csv` file and how many records look acceptable, with the rules the server applies
 * (a missing `input` or a value over its size limit is rejected, a CSV cell holding a JSON object
 * or array is that value, blank lines are skipped). The server stays the authority.
 *
 * The whole file (at most 5 MB) is read once: JSONL lines go through the native `JSON.parse`, and
 * CSV is scanned with `indexOf` and slices rather than character by character.
 */

import { MAX_METADATA_BYTES, MAX_PAYLOAD_BYTES, jsonBytes } from "./item-values";

export type UploadKind = "jsonl" | "csv";

export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
export const PREVIEW_ROWS = 5;

export interface PreviewRow {
  /** 1-based line the record ends on, as the server reports it. */
  line: number;
  input: unknown;
  /** Null when the record has no expected output. */
  expectedOutput: unknown;
  /** Why the server would reject the record; null when it looks fine. */
  problem: string | null;
}

export interface UploadPreview {
  kind: UploadKind;
  /** The first `PREVIEW_ROWS` records. */
  rows: PreviewRow[];
  /** Every non-blank record in the file. */
  total: number;
  /** The records that look acceptable: what "Upload N items" counts. */
  valid: number;
  /** A problem with the whole file (no `input` column, no records); null otherwise. */
  fileProblem: string | null;
}

interface Fields {
  input: unknown;
  expected: unknown;
  metadata: Record<string, unknown>;
}

/** The upload kind from the file name, or null for any other extension. */
export function uploadKind(filename: string): UploadKind | null {
  const name = filename.toLowerCase();
  if (name.endsWith(".jsonl")) return "jsonl";
  if (name.endsWith(".csv")) return "csv";
  return null;
}

export function previewUpload(kind: UploadKind, text: string): UploadPreview {
  const body = text.startsWith("﻿") ? text.slice(1) : text;
  const preview = kind === "jsonl" ? previewJsonl(body) : previewCsv(body);
  if (preview.fileProblem === null && preview.total === 0) {
    return { ...preview, fileProblem: "The file has no items. Each line needs an input." };
  }
  return preview;
}

/** Collects the first rows and the counts as records stream past. */
function collector(kind: UploadKind) {
  const preview: UploadPreview = { kind, rows: [], total: 0, valid: 0, fileProblem: null };
  return {
    preview,
    add: (line: number, fields: Fields | string) => {
      const row: PreviewRow =
        typeof fields === "string"
          ? { line, input: null, expectedOutput: null, problem: fields }
          : {
              line,
              input: fields.input,
              expectedOutput: fields.expected,
              problem: recordProblem(fields),
            };
      preview.total += 1;
      if (row.problem === null) preview.valid += 1;
      if (preview.rows.length < PREVIEW_ROWS) preview.rows.push(row);
    },
  };
}

/** The server's per-record checks (`DatasetItemIn`), in its words. */
function recordProblem({ input, expected, metadata }: Fields): string | null {
  if (input === null) return "input is required";
  if (jsonBytes(input) > MAX_PAYLOAD_BYTES) return "input is larger than 64 KB";
  if (jsonBytes(expected) > MAX_PAYLOAD_BYTES) return "expected_output is larger than 64 KB";
  if (jsonBytes(metadata) > MAX_METADATA_BYTES) return "metadata is larger than 8 KB";
  return null;
}

function previewJsonl(text: string): UploadPreview {
  const { preview, add } = collector("jsonl");
  text.split("\n").forEach((line, index) => {
    if (line.trim()) add(index + 1, jsonlFields(line));
  });
  return preview;
}

function jsonlFields(text: string): Fields | string {
  let record: unknown;
  try {
    record = JSON.parse(text);
  } catch {
    return "not valid JSON";
  }
  if (typeof record !== "object" || record === null || Array.isArray(record)) {
    return "not a JSON object";
  }
  const fields = record as Record<string, unknown>;
  // As the server: an object under `metadata` is the base, every other key joins it.
  const base = fields.metadata;
  const baseIsObject = typeof base === "object" && base !== null && !Array.isArray(base);
  const metadata: Record<string, unknown> = baseIsObject
    ? { ...(base as Record<string, unknown>) }
    : {};
  for (const [key, value] of Object.entries(fields)) {
    const reserved = key === "input" || key === "expected_output" || key === "metadata";
    if (!reserved || (key === "metadata" && !baseIsObject)) metadata[key] = value;
  }
  return { input: fields.input ?? null, expected: fields.expected_output ?? null, metadata };
}

function previewCsv(text: string): UploadPreview {
  const { preview, add } = collector("csv");
  const records = csvRecords(text);
  const header = records.next();
  const names =
    header.done || header.value.unreadable ? null : header.value.cells.map((name) => name.trim());
  if (names?.includes("input") !== true) {
    return { ...preview, fileProblem: "The header row needs an input column." };
  }
  for (const { cells, line, unreadable } of records) {
    if (unreadable) {
      // As the server: its reader would resume inside the broken cell, so it stops here.
      add(line, "unreadable record; the rest is skipped");
      break;
    }
    if (cells.some((cell) => cell.trim())) add(line, csvFields(names, cells));
  }
  return preview;
}

function csvFields(names: string[], cells: string[]): Fields {
  const metadata: Record<string, unknown> = {};
  let input: unknown = null;
  let expected: unknown = null;
  names.forEach((name, index) => {
    const cell = cells[index] ?? "";
    if (name === "input") input = cellValue(cell);
    else if (name === "expected_output") expected = cellValue(cell);
    else if (name) metadata[name] = cell;
  });
  return { input, expected, metadata };
}

/** The JSON object or array a cell holds, else its text; null for a blank cell. */
export function cellValue(cell: string): unknown {
  const stripped = cell.trim();
  if (!stripped) return null;
  if (stripped.startsWith("{") || stripped.startsWith("[")) {
    try {
      const value: unknown = JSON.parse(stripped);
      if (typeof value === "object" && value !== null) return value;
    } catch {
      // Not JSON after all: kept as text, as the server does.
    }
  }
  return cell;
}

interface CsvRecord {
  cells: string[];
  /** The physical line the record ends on. */
  line: number;
  /** Python's csv module refuses the record (and the reader stops): see `csvRecords`. */
  unreadable: boolean;
}

/** The server's `csv.field_size_limit`; Python counts characters, not UTF-16 units. */
const FIELD_LIMIT = 64 * 1024;

function overFieldLimit(value: string): boolean {
  // A string never has fewer UTF-16 units than characters, so only a long cell is counted.
  return value.length > FIELD_LIMIT && Array.from(value).length > FIELD_LIMIT;
}

/**
 * Whether an unquoted stretch holds a carriage return Python's csv refuses ("new-line character
 * seen in unquoted field"): one that is not part of the record's line end.
 */
function hasBareCr(stretch: string, endsRecord: boolean): boolean {
  return (endsRecord ? stretch.replace(/\r+$/, "") : stretch).includes("\r");
}

const QUOTE = 34;
const COMMA = 44;
const CR = 13;
const DELIMITER = /,|\r?\n/g;

/** Where the cell starting at `from` ends: the next comma or line break, or the end of text. */
function cellEnd(text: string, from: number): number {
  DELIMITER.lastIndex = from;
  return DELIMITER.exec(text)?.index ?? text.length;
}

/**
 * A quoted cell starting at `from` (on its opening quote): its text, the unquoted stretch after
 * the closing quote, and where it ends.
 */
function quotedCell(text: string, from: number): { value: string; tail: string; end: number } {
  let value = "";
  let start = from + 1;
  for (;;) {
    const quote = text.indexOf('"', start);
    if (quote === -1) return { value: value + text.slice(start), tail: "", end: text.length };
    value += text.slice(start, quote);
    if (text.charCodeAt(quote + 1) !== QUOTE) {
      // Anything between the closing quote and the delimiter stays in the cell, as csv does.
      const end = cellEnd(text, quote + 1);
      const tail = text.slice(quote + 1, end);
      return { value: value + tail, tail, end };
    }
    value += '"';
    start = quote + 2;
  }
}

/**
 * RFC 4180 records: quoted cells may hold commas, doubled quotes and line breaks. A record ends
 * at a line break outside quotes (`\n` or `\r\n`). Like the server's Python `csv` reader, a
 * carriage return elsewhere outside quotes, or a cell over 65,536 characters, makes the record
 * unreadable, and nothing after it is read.
 */
export function* csvRecords(text: string): Generator<CsvRecord> {
  let position = 0;
  let line = 1;
  while (position < text.length) {
    const cells: string[] = [];
    for (;;) {
      let value: string;
      let unquoted: string;
      if (text.charCodeAt(position) === QUOTE) {
        const quoted = quotedCell(text, position);
        value = quoted.value;
        unquoted = quoted.tail;
        line += value.split("\n").length - 1;
        position = quoted.end;
      } else {
        const end = cellEnd(text, position);
        value = text.slice(position, end);
        unquoted = value;
        position = end;
      }
      const endsRecord = position >= text.length || text.charCodeAt(position) !== COMMA;
      if (hasBareCr(unquoted, endsRecord) || overFieldLimit(value)) {
        yield { cells, line, unreadable: true };
        return;
      }
      cells.push(value);
      if (position < text.length && text.charCodeAt(position) === COMMA) {
        position += 1;
        continue;
      }
      position += text.charCodeAt(position) === CR ? 2 : 1;
      yield { cells, line, unreadable: false };
      line += 1;
      break;
    }
  }
}
