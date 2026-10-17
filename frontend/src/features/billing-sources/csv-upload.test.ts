import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import {
  REQUIRED_COLUMNS_NOTE,
  checkCsvFile,
  csvErrorsOf,
  csvErrorSummary,
  csvTemplate,
  formatFileSize,
} from "./csv-upload";

function file(bytes: number, name = "costs.csv"): File {
  return new File([new Uint8Array(bytes)], name, { type: "text/csv" });
}

describe("checkCsvFile", () => {
  it("accepts a CSV up to 5 MB", () => {
    expect(checkCsvFile(file(5 * 1024 * 1024))).toBeNull();
  });

  it("refuses an empty file, a larger one and another file type before sending it", () => {
    expect(checkCsvFile(file(0))).toBe("The file is empty.");
    expect(checkCsvFile(file(5 * 1024 * 1024 + 1))).toBe("The file is larger than 5 MB.");
    expect(checkCsvFile(new File(["x"], "costs.xlsx", { type: "application/vnd.ms-excel" }))).toBe(
      "Choose a .csv file.",
    );
  });
});

describe("formatFileSize", () => {
  it("uses bytes, KB and MB", () => {
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(48 * 1024)).toBe("48 KB");
    expect(formatFileSize(1.5 * 1024 * 1024)).toBe("1.5 MB");
  });
});

describe("csvErrorsOf", () => {
  it("reads the file errors of a refused upload", () => {
    const error = new ApiError(422, {
      code: "VALIDATION_ERROR",
      errors: [
        { field: "file", row: 14, column: "amount_usd", message: "amount_usd must be >= 0" },
        { field: "file", row: 58, column: null, message: "the row has 9 cells" },
      ],
    });
    expect(csvErrorsOf(error)).toEqual([
      { row: 14, column: "amount_usd", message: "amount_usd must be >= 0" },
      { row: 58, column: null, message: "the row has 9 cells" },
    ]);
  });

  it("is null for any other failure", () => {
    expect(csvErrorsOf(new ApiError(413, { code: "PAYLOAD_TOO_LARGE" }))).toBeNull();
    expect(csvErrorsOf(new Error("boom"))).toBeNull();
  });
});

describe("csvErrorSummary", () => {
  it("counts the rows at fault, not the errors", () => {
    const errors = [
      { row: 14, column: "amount_usd", message: "a" },
      { row: 14, column: "day", message: "b" },
      { row: 22, column: "provider", message: "c" },
    ];
    expect(csvErrorSummary("costs.csv", errors)).toBe(
      "2 rows of costs.csv have errors. Fix them and upload the file again; no line is kept until the whole file is valid.",
    );
    expect(csvErrorSummary("costs.csv", errors.slice(0, 1))).toBe(
      "1 row of costs.csv has errors. Fix it and upload the file again; no line is kept until the whole file is valid.",
    );
  });

  it("speaks of the file when only the file as a whole is at fault", () => {
    expect(csvErrorSummary("costs.csv", [{ row: 0, column: "day", message: "missing" }])).toBe(
      "costs.csv can't be imported as it is. Fix it and upload the file again; no line is kept until the whole file is valid.",
    );
  });
});

describe("REQUIRED_COLUMNS_NOTE", () => {
  it("says the header lists every column, since the importer refuses a missing one", () => {
    expect(REQUIRED_COLUMNS_NOTE).toBe(
      "The header must list all ten columns: day, provider, line_kind, amount_usd, model, token_type, service_tier, quantity, description, provider_scope. Only model through provider_scope may be left blank. Spanlight keeps the lines, not the file.",
    );
  });
});

describe("csvTemplate", () => {
  it("is the header row of the documented format", () => {
    expect(csvTemplate()).toBe(
      "day,provider,line_kind,amount_usd,model,token_type,service_tier,quantity,description,provider_scope\n",
    );
  });
});
