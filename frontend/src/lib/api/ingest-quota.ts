import { api } from "./client";
import { projectPath } from "./paths";
import type { CompletenessQuery, IngestQuota } from "./types";

/** Ingest quota: the project's caps, today's usage and the spans its caps refused. `project:read`. */
export const ingestQuotaApi = {
  get: (
    projectId: string,
    query: CompletenessQuery = {},
    signal?: AbortSignal,
  ): Promise<IngestQuota> =>
    api.get<IngestQuota>(`${projectPath(projectId)}/ingest-quota`, { ...query }, signal),
};
