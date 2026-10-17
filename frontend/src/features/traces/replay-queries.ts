import { useQuery } from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell";
import { orgsApi, queryKeys, replaysApi } from "@/lib/api";

/** Rows the Replays tab shows; older replays of one span are rare enough to leave out. */
const REPLAYS_PAGE_SIZE = 50;

/** How the span would be replayed, or why it cannot be. No provider is called. */
export function useReplaySourceQuery(
  traceId: string,
  spanId: string,
  options: { enabled: boolean },
) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).replays.source(traceId, spanId),
    queryFn: () => replaysApi.source(projectId, traceId, spanId),
    enabled: options.enabled,
  });
}

/** The project's replay budget this month; a zero budget means replay is turned off. */
export function useReplayBudgetQuery(options: { enabled: boolean }) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).replays.budget,
    queryFn: () => replaysApi.budget(projectId),
    staleTime: 60_000,
    enabled: options.enabled,
  });
}

/** The span's replays, newest first. */
export function useSpanReplaysQuery(traceId: string, spanId: string) {
  const { projectId } = useProjectParams();
  const query = { trace_id: traceId, span_id: spanId, limit: REPLAYS_PAGE_SIZE };
  return useQuery({
    queryKey: queryKeys.project(projectId).replays.list(query),
    queryFn: () => replaysApi.list(projectId, query),
  });
}

/** The org's members, to name who ran each replay. Same cache entry as the Members page. */
export function useReplayMembersQuery() {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).members,
    queryFn: () => orgsApi.members(orgId),
    staleTime: 5 * 60_000,
  });
}
