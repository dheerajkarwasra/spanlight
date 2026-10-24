import type {
  AttributionQuery,
  AttributionResponse,
  AttributionSeries,
  AttributionSeriesQuery,
} from "./attribution-types";
import { api } from "./client";
import { projectPath } from "./paths";

/** Spend and usage grouped by one dimension. Reads need `project:read`. */
export const attributionApi = {
  /** Sorted by `sort` descending, unknowns last, keyset-paginated; `none`, `other` and `totals` ride on every page. */
  read: (projectId: string, query: AttributionQuery): Promise<AttributionResponse> =>
    api.get<AttributionResponse>(`${projectPath(projectId)}/attribution`, { ...query }),
  /** The Spend chart; 422 `KEY_NOT_PROMOTED` like the list for an unpromoted key over 24 hours. */
  series: (projectId: string, query: AttributionSeriesQuery): Promise<AttributionSeries> =>
    api.get<AttributionSeries>(`${projectPath(projectId)}/attribution/series`, { ...query }),
};
