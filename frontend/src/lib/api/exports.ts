import type { AttributionGroupBy } from "./attribution-types";
import { api } from "./client";
import { projectPath } from "./paths";
import type {
  AttributionExportFilters,
  ExportFilters,
  ExportFormat,
  Page,
  TraceExport,
} from "./types";

/** One row per trace matching `filters`; `kind` may be left out, as before attribution exports. */
export interface TraceExportInput {
  kind?: "traces";
  format: ExportFormat;
  filters: ExportFilters;
}

/**
 * The attribution table of the window as CSV. `key` is required with `group_by: metadata` and
 * refused otherwise; a window over 24 hours needs a promoted key (`422 KEY_NOT_PROMOTED`).
 */
export interface AttributionExportInput {
  kind: "attribution";
  format: "csv";
  group_by: AttributionGroupBy;
  key?: string;
  filters: AttributionExportFilters;
}

export type CreateExportInput = TraceExportInput | AttributionExportInput;

export interface ExportListQuery {
  limit?: number;
  cursor?: string | null;
}

/**
 * A fresh `Idempotency-Key`. Make one per user action and reuse it when retrying that action, so
 * a retry returns the same export instead of queueing a second one. Send a new key once the
 * request itself changes: the same key with a different body is `422 IDEMPOTENCY_MISMATCH`.
 */
export function newIdempotencyKey(): string {
  // `getRandomValues` also works on a self-hosted dashboard served over plain HTTP, where
  // `randomUUID` is unavailable (it needs a secure context).
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function exportsPath(projectId: string): string {
  return `${projectPath(projectId)}/exports`;
}

export const exportsApi = {
  /** `409 NOT_CONFIGURED` when the server has no object storage. */
  create: (
    projectId: string,
    input: CreateExportInput,
    idempotencyKey: string,
  ): Promise<TraceExport> =>
    api.post<TraceExport>(exportsPath(projectId), input, { "Idempotency-Key": idempotencyKey }),
  /** Newest first. */
  list: (projectId: string, query: ExportListQuery = {}): Promise<Page<TraceExport>> =>
    api.get<Page<TraceExport>>(exportsPath(projectId), { ...query }),
  /**
   * One export. Its `download_url` lasts an hour, so fetch it again for a fresh link instead of
   * keeping the one from the list.
   */
  get: (projectId: string, exportId: string): Promise<TraceExport> =>
    api.get<TraceExport>(`${exportsPath(projectId)}/${encodeURIComponent(exportId)}`),
};
