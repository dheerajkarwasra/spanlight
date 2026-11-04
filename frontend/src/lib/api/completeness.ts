import { api } from "./client";
import { projectPath } from "./paths";
import type { Completeness, CompletenessQuery } from "./types";

/** Telemetry completeness: stored spans against the spans counted as lost. `project:read`. */
export const completenessApi = {
  get: (
    projectId: string,
    query: CompletenessQuery = {},
    signal?: AbortSignal,
  ): Promise<Completeness> =>
    api.get<Completeness>(`${projectPath(projectId)}/telemetry-completeness`, { ...query }, signal),
};
