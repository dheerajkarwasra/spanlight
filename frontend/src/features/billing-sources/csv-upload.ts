import { isApiError } from "@/lib/api";

/** The server's limit (spec §8.4); a larger file is refused here before it is sent. */
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

/** The §8.4 header in its documented order (a file may use any order). */
export const CSV_COLUMNS = [
  "day",
  "provider",
  "line_kind",
  "amount_usd",
  "model",
  "token_type",
  "service_tier",
  "quantity",
  "description",
  "provider_scope",
] as const;

export const REQUIRED_COLUMNS_NOTE =
  "The header must list all ten columns: day, provider, line_kind, amount_usd, model, token_type, service_tier, quantity, description, provider_scope. Only model through provider_scope may be left blank. Spanlight keeps the lines, not the file.";

/** One reason the server refused the file: a row (0 = the whole file) and a column, or none. */
export interface CsvRowError {
  row: number;
  column: string | null;
  message: string;
}

/** Why a picked file cannot be sent, or null when it can. */
export function checkCsvFile(file: File): string | null {
  const isCsv = file.name.toLowerCase().endsWith(".csv") || file.type === "text/csv";
  if (!isCsv) {
    return "Choose a .csv file.";
  }
  if (file.size === 0) {
    return "The file is empty.";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return "The file is larger than 5 MB.";
  }
  return null;
}

/** "512 B", "48 KB", "1.5 MB". */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${String(bytes)} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${String(Math.round(bytes / 1024))} KB`;
  }
  return `${String(Math.round((bytes / (1024 * 1024)) * 10) / 10)} MB`;
}

/** The file errors of a `422` upload, or null when the failure was something else. */
export function csvErrorsOf(error: unknown): CsvRowError[] | null {
  if (!isApiError(error) || error.status !== 422) {
    return null;
  }
  const fileErrors = error.fieldErrors.filter((fieldError) => fieldError.field === "file");
  if (fileErrors.length === 0) {
    return null;
  }
  return fileErrors.map((fieldError) => ({
    row: fieldError.row ?? 0,
    column: fieldError.column ?? null,
    message: fieldError.message,
  }));
}

const KEEP_NOTHING = "no line is kept until the whole file is valid.";

/** The danger callout's sentence: how many rows are at fault, or the file as a whole. */
export function csvErrorSummary(fileName: string, errors: readonly CsvRowError[]): string {
  const rows = new Set(errors.filter((error) => error.row > 0).map((error) => error.row)).size;
  if (rows === 0) {
    return `${fileName} can't be imported as it is. Fix it and upload the file again; ${KEEP_NOTHING}`;
  }
  if (rows === 1) {
    return `1 row of ${fileName} has errors. Fix it and upload the file again; ${KEEP_NOTHING}`;
  }
  return `${String(rows)} rows of ${fileName} have errors. Fix them and upload the file again; ${KEEP_NOTHING}`;
}

/** The empty template: the header row the importer reads. */
export function csvTemplate(): string {
  return `${CSV_COLUMNS.join(",")}\n`;
}

/** Saves the template as a file, built in the browser: there is nothing to fetch. */
export function downloadCsvTemplate(): void {
  const url = URL.createObjectURL(new Blob([csvTemplate()], { type: "text/csv" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "spanlight-billing-template.csv";
  link.click();
  // Revoked on the next task: some browsers start the download only after `click()` returns.
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}
