import { describe, expect, it } from "vitest";

import { MAX_PAYLOAD_BYTES } from "./item-values";
import { PREVIEW_ROWS, cellValue, previewUpload, uploadKind } from "./upload-preview";

// The expected values below are what the server's importer (`app/datasets/importers.py`) does
// with the same bytes: the preview promises "Upload N items", so it must count what the server
// will accept.

function inputs(kind: "csv" | "jsonl", text: string): unknown[] {
  return previewUpload(kind, text).rows.map((row) => row.input);
}

describe("uploadKind", () => {
  it("reads the extension case-insensitively and refuses anything else", () => {
    expect(uploadKind("Data.CSV")).toBe("csv");
    expect(uploadKind("rows.JSONL")).toBe("jsonl");
    expect(uploadKind("rows.json")).toBeNull();
    expect(uploadKind("rows.csv.txt")).toBeNull();
  });
});

describe("CSV preview", () => {
  it("strips a UTF-8 byte order mark and handles CRLF line ends", () => {
    const preview = previewUpload("csv", "\uFEFFinput,expected_output\r\nhello,world\r\n");
    expect(preview.fileProblem).toBeNull();
    expect(preview.rows).toEqual([
      { line: 2, input: "hello", expectedOutput: "world", problem: null },
    ]);
  });

  it("keeps quoted commas and line breaks inside one cell and counts the physical line", () => {
    const preview = previewUpload("csv", 'input,tag\n"a, b\nc",x\nnext,y\n');
    expect(preview.rows.map((row) => [row.line, row.input])).toEqual([
      [3, "a, b\nc"],
      [4, "next"],
    ]);
    expect(preview.valid).toBe(2);
  });

  it("keeps a CRLF inside a quoted cell", () => {
    expect(inputs("csv", 'input,t\r\n"a\r\nb",x\r\nc,y\r\n')).toEqual(["a\r\nb", "c"]);
  });

  it("unescapes doubled quotes", () => {
    expect(inputs("csv", 'input\n"say ""hi"""\n')).toEqual(['say "hi"']);
  });

  it("keeps text after a closing quote in the cell, as Python's csv module does", () => {
    expect(inputs("csv", 'input\n"ab"cd,ef\n')).toEqual(["abcd"]);
  });

  it("reads an unterminated quote to the end of the file", () => {
    expect(inputs("csv", 'input\n"open\nmore\n')).toEqual(["open\nmore\n"]);
  });

  it("stores a cell holding a JSON object or array as that value, other text as text", () => {
    const preview = previewUpload("csv", 'input,expected_output\n"{""a"": 1}","[1,2]"\n');
    expect(preview.rows[0]?.input).toEqual({ a: 1 });
    expect(preview.rows[0]?.expectedOutput).toEqual([1, 2]);
    expect(inputs("csv", "input\n{not json\n")).toEqual(["{not json"]);
  });

  it("skips blank and whitespace-only records", () => {
    const preview = previewUpload("csv", "input\n\n , \nx\n");
    expect(preview.total).toBe(1);
    expect(preview.rows[0]).toMatchObject({ line: 4, input: "x" });
  });

  it("matches header names after trimming spaces", () => {
    const preview = previewUpload("csv", " input , expected_output \nx,y\n");
    expect(preview.rows[0]).toMatchObject({ input: "x", expectedOutput: "y", problem: null });
  });

  it("rejects a file whose header has no input column", () => {
    const preview = previewUpload("csv", "prompt\nx\n");
    expect(preview.fileProblem).toBe("The header row needs an input column.");
    expect(preview.valid).toBe(0);
  });

  it("flags a record with an empty input as the server does, and does not count it", () => {
    const preview = previewUpload("csv", "input,tag\n,meta\nok,meta\n");
    expect(preview.rows[0]).toMatchObject({ line: 2, problem: "input is required" });
    expect(preview.total).toBe(2);
    expect(preview.valid).toBe(1);
  });

  it("flags an input over 64 KB", () => {
    const big = "a".repeat(MAX_PAYLOAD_BYTES);
    const preview = previewUpload("csv", `input\n${big}\n`);
    expect(preview.rows[0]?.problem).toBe("input is larger than 64 KB");
    expect(preview.valid).toBe(0);
  });

  it("reports a header-only file as having no items", () => {
    expect(previewUpload("csv", "input\n").fileProblem).toBe(
      "The file has no items. Each line needs an input.",
    );
  });

  it("previews only the first rows but counts every record", () => {
    const body = Array.from({ length: PREVIEW_ROWS + 3 }, (_, index) => `row${String(index)}`);
    const preview = previewUpload("csv", `input\n${body.join("\n")}\n`);
    expect(preview.rows).toHaveLength(PREVIEW_ROWS);
    expect(preview.total).toBe(PREVIEW_ROWS + 3);
    expect(preview.valid).toBe(PREVIEW_ROWS + 3);
  });

  // Finding T8-01: the server's csv reader rejects a bare carriage return in an unquoted cell
  // ("unreadable record; the rest is skipped") and stops reading, so it stores nothing after it.
  // The preview accepted the record and the rest, so "Upload N items" over-promised.
  it("stops at a bare carriage return the server cannot read", () => {
    const preview = previewUpload("csv", "input\nfirst\na\rb\nc\n");
    expect(preview.valid).toBe(1);
    expect(preview.total).toBe(2);
    expect(preview.rows.map((row) => [row.line, row.problem])).toEqual([
      [2, null],
      [3, "unreadable record; the rest is skipped"],
    ]);
  });

  it("treats an unreadable header row as a missing input column, as the server does", () => {
    expect(previewUpload("csv", "input,no\rte\nx\n").fileProblem).toBe(
      "The header row needs an input column.",
    );
  });

  it("stops at a carriage return before a comma or after a closing quote", () => {
    expect(previewUpload("csv", "input\na\r,b\n").valid).toBe(0);
    expect(previewUpload("csv", 'input\n"x"\ry\n').valid).toBe(0);
  });

  it("keeps a carriage return inside quotes and before a line feed", () => {
    expect(inputs("csv", 'input\r\n"a\rb"\r\nc\r\n')).toEqual(["a\rb", "c"]);
  });

  // Finding T8-01 (same cause): a CSV cell over the csv module's 65,536-character field limit
  // also stops the server's reader, so the records after it are never stored.
  it("stops at a cell over the server's 65,536-character field limit", () => {
    const preview = previewUpload("csv", `input\n${"a".repeat(65_537)}\nok\nok2\n`);
    expect(preview.valid).toBe(0);
    expect(preview.rows.map((row) => row.problem)).toEqual([
      "unreadable record; the rest is skipped",
    ]);
  });

  it("counts the field limit in characters, not UTF-16 units", () => {
    // 65,536 emoji are 131,072 UTF-16 units but exactly the server's limit in characters. The
    // cell sits past the header's columns, so the server reads and then ignores it.
    const preview = previewUpload("csv", `input\nok,${"😀".repeat(65_536)}\n`);
    expect(preview.rows.map((row) => row.problem)).toEqual([null]);
  });
});

describe("JSONL preview", () => {
  it("strips a byte order mark and tolerates CRLF line ends", () => {
    expect(inputs("jsonl", '\uFEFF{"input":"x"}\r\n{"input":"y"}\r\n')).toEqual(["x", "y"]);
  });

  it("names a line that is not JSON and one that is not an object", () => {
    const preview = previewUpload("jsonl", 'nope\n[1]\n{"input":"ok"}\n');
    expect(preview.rows.map((row) => [row.line, row.problem])).toEqual([
      [1, "not valid JSON"],
      [2, "not a JSON object"],
      [3, null],
    ]);
    expect(preview.valid).toBe(1);
  });

  it("requires input and lets the last duplicate key win", () => {
    expect(previewUpload("jsonl", '{"input": null}\n').rows[0]?.problem).toBe("input is required");
    expect(previewUpload("jsonl", '{"input":1,"input":null}\n').valid).toBe(0);
  });

  it("accepts an empty string and false as inputs (they are not missing)", () => {
    const preview = previewUpload("jsonl", '{"input":""}\n{"input":false}\n');
    expect(preview.valid).toBe(2);
  });

  it("refuses NaN, which the server cannot store as JSON", () => {
    expect(previewUpload("jsonl", '{"input": NaN}\n').valid).toBe(0);
  });

  it("flags metadata over 8 KB, counting keys outside `metadata` too", () => {
    const line = JSON.stringify({ input: "x", note: "n".repeat(8 * 1024) });
    expect(previewUpload("jsonl", `${line}\n`).rows[0]?.problem).toBe(
      "metadata is larger than 8 KB",
    );
  });

  it("measures the 64 KB input limit in UTF-8 bytes", () => {
    // 3 bytes per character: far under 64 K characters but over 64 KB once encoded.
    const line = JSON.stringify({ input: "€".repeat(22_000) });
    expect(previewUpload("jsonl", `${line}\n`).rows[0]?.problem).toBe("input is larger than 64 KB");
  });

  it("reports a file of blank lines as having no items", () => {
    expect(previewUpload("jsonl", "\n  \n").fileProblem).toBe(
      "The file has no items. Each line needs an input.",
    );
  });
});

describe("cellValue", () => {
  it("is null for a blank cell and keeps the original text (with spaces) otherwise", () => {
    expect(cellValue("   ")).toBeNull();
    expect(cellValue("  hi ")).toBe("  hi ");
  });

  it("keeps a JSON scalar as text", () => {
    expect(cellValue("42")).toBe("42");
    expect(cellValue('"quoted"')).toBe('"quoted"');
  });
});
