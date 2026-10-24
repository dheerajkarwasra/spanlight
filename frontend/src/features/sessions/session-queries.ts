import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";

import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import { projectsApi, queryKeys, type SessionListQuery } from "@/lib/api";

import type { SessionsSearch } from "./sessions-search";

export const SESSION_PAGE_SIZE = 50;
const SESSION_TURNS_PAGE_SIZE = 100;

/** The list state the URL carries besides the window. */
export type SessionsListState = Pick<SessionsSearch, "sort" | "order" | "q">;

/**
 * The sessions endpoint's query parameters for the URL's list state, a time window and an optional
 * environment. Previews are asked for only while the Last turn column shows (spec ruling 4).
 */
export function toSessionListQuery(
  { sort, order, q }: SessionsListState,
  window: { from: string; to: string },
  environment: string | undefined,
  preview: boolean,
): Omit<SessionListQuery, "cursor"> {
  return {
    from: window.from,
    to: window.to,
    environment,
    q,
    sort,
    order,
    preview: preview || undefined,
    limit: SESSION_PAGE_SIZE,
  };
}

/**
 * Sessions active in the selected time range and environment, matching `q` and ranked by `sort`
 * and `order`. A session that spans environments counts only the traces of the selected one.
 */
export function useSessionListQuery(list: SessionsListState, preview: boolean) {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const query = toSessionListQuery(list, range, environment, preview);

  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).sessions(query),
    queryFn: ({ pageParam }) => projectsApi.sessions(projectId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    placeholderData: keepPreviousData,
  });
}

/**
 * Every trace of one session with the spans its conversation needs, newest first, one request per
 * page. Independent of the page's time range: a session's traces are listed for as long as
 * they are retained.
 */
export function useSessionTurnsQuery(sessionId: string) {
  const { projectId } = useProjectParams();
  const query = { limit: SESSION_TURNS_PAGE_SIZE };

  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).sessionTurns(sessionId, query),
    queryFn: ({ pageParam }) =>
      projectsApi.sessionTurns(projectId, sessionId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}
