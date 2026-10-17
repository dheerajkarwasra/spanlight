import type { AttributionQuery, AttributionResponse } from "./attribution-types";
import { api } from "./client";
import { projectPath } from "./paths";

/** Spend and usage grouped by one dimension. Reads need `project:read`. */
export const attributionApi = {
  /** Sorted by `sort` descending, unknowns last, keyset-paginated; `none`, `other` and `totals` ride on every page. */
  read: (projectId: string, query: AttributionQuery): Promise<AttributionResponse> =>
    api.get<AttributionResponse>(`${projectPath(projectId)}/attribution`, { ...query }),
};
