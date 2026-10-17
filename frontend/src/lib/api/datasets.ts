import { api } from "./client";
import { projectPath } from "./paths";
import type { Page } from "./types";

export interface Dataset {
  id: string;
  name: string;
  description: string | null;
  item_count: number;
  created_at: string;
  updated_at: string;
}

export interface DatasetCreate {
  name: string;
  description?: string | null;
}

export interface DatasetUpdate {
  description?: string | null;
}

export interface DatasetListQuery {
  name?: string | undefined;
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

/** `expected_output` is null when the item has none, never an empty value. */
export interface DatasetItem {
  id: string;
  input: unknown;
  expected_output: unknown;
  metadata: Record<string, unknown>;
  source_trace_id: string | null;
  source_span_id: string | null;
  created_at: string;
}

export interface DatasetItemInput {
  input: unknown;
  expected_output?: unknown;
  metadata?: Record<string, unknown>;
  source_trace_id?: string | null;
  source_span_id?: string | null;
}

export interface DatasetItemUpdate {
  expected_output?: unknown;
  metadata?: Record<string, unknown> | null;
}

export interface ItemListQuery {
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

export interface ItemsAdded {
  accepted: number;
}

export interface TracesAdded {
  accepted: number;
  rejected: { trace_id: string; reason: string }[];
  /** The new items' ids, in the order of the accepted traces. */
  item_ids: string[];
}

/** A `.jsonl` or `.csv` upload; each bad line is listed and the rest is accepted. */
export interface UploadResult {
  accepted: number;
  rejected: { line: number; reason: string }[];
}

function datasetsPath(projectId: string): string {
  return `${projectPath(projectId)}/datasets`;
}

function datasetPath(projectId: string, datasetId: string): string {
  return `${datasetsPath(projectId)}/${encodeURIComponent(datasetId)}`;
}

/**
 * Datasets under `/projects/{id}/datasets`. Reads need `project:read`; writes need
 * `datasets:write` (member). Errors: `409 NAME_TAKEN`, `409 DATASET_IN_USE`, `422 DATASET_FULL`,
 * `415 UNSUPPORTED_FILE`,
 * `413 PAYLOAD_TOO_LARGE` (files over 5 MB).
 */
export const datasetsApi = {
  list: (projectId: string, query: DatasetListQuery = {}): Promise<Page<Dataset>> =>
    api.get<Page<Dataset>>(datasetsPath(projectId), { ...query }),
  get: (projectId: string, datasetId: string): Promise<Dataset> =>
    api.get<Dataset>(datasetPath(projectId, datasetId)),
  create: (projectId: string, input: DatasetCreate): Promise<Dataset> =>
    api.post<Dataset>(datasetsPath(projectId), input),
  update: (projectId: string, datasetId: string, update: DatasetUpdate): Promise<Dataset> =>
    api.patch<Dataset>(datasetPath(projectId, datasetId), update),
  /** `409 DATASET_IN_USE` while an experiment ran on it. */
  delete: (projectId: string, datasetId: string): Promise<void> =>
    api.delete(datasetPath(projectId, datasetId)),

  items: (
    projectId: string,
    datasetId: string,
    query: ItemListQuery = {},
  ): Promise<Page<DatasetItem>> =>
    api.get<Page<DatasetItem>>(`${datasetPath(projectId, datasetId)}/items`, { ...query }),
  /** Adds the items (201). */
  addItems: (
    projectId: string,
    datasetId: string,
    items: DatasetItemInput[],
  ): Promise<ItemsAdded> =>
    api.post<ItemsAdded>(`${datasetPath(projectId, datasetId)}/items`, { items }),
  /** One item per trace (201), from the trace or, with `spanId`, from that span of each. */
  addItemsFromTraces: (
    projectId: string,
    datasetId: string,
    traceIds: string[],
    spanId?: string | null,
  ): Promise<TracesAdded> =>
    api.post<TracesAdded>(`${datasetPath(projectId, datasetId)}/items/from-traces`, {
      trace_ids: traceIds,
      ...(spanId ? { span_id: spanId } : {}),
    }),
  /** Multipart upload, form field `file`; a `.jsonl` or `.csv` of UTF-8 text, at most 5 MB. */
  uploadItems: (projectId: string, datasetId: string, file: File): Promise<UploadResult> => {
    const form = new FormData();
    form.append("file", file);
    return api.upload<UploadResult>(`${datasetPath(projectId, datasetId)}/items/upload`, form);
  },
  updateItem: (
    projectId: string,
    datasetId: string,
    itemId: string,
    update: DatasetItemUpdate,
  ): Promise<DatasetItem> =>
    api.patch<DatasetItem>(
      `${datasetPath(projectId, datasetId)}/items/${encodeURIComponent(itemId)}`,
      update,
    ),
  deleteItem: (projectId: string, datasetId: string, itemId: string): Promise<void> =>
    api.delete(`${datasetPath(projectId, datasetId)}/items/${encodeURIComponent(itemId)}`),
};
