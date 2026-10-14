import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import { projectsApi, queryKeys, type SessionListQuery, type TraceListQuery } from "@/lib/api";
import { MAX_WINDOW_MS } from "@/lib/time-range";

export const SESSION_PAGE_SIZE = 50;
const SESSION_TRACES_PAGE_SIZE = 100;

/** The sessions endpoint's query parameters for a time window and an optional environment. */
export function toSessionListQuery(
  window: { from: string; to: string },
  environment: string | undefined,
): Omit<SessionListQuery, "cursor"> {
  return { from: window.from, to: window.to, environment, limit: SESSION_PAGE_SIZE };
}

/**
 * Sessions active in the selected time range and environment. A session that spans
 * environments counts only the traces of the selected one.
 */
export function useSessionListQuery() {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const query = toSessionListQuery(range, environment);

  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).sessions(query),
    queryFn: ({ pageParam }) => projectsApi.sessions(projectId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    placeholderData: keepPreviousData,
  });
}

function lookbackWindow(): { from: string; to: string } {
  const to = new Date();
  to.setSeconds(0, 0);
  return {
    from: new Date(to.getTime() - MAX_WINDOW_MS).toISOString(),
    to: to.toISOString(),
  };
}

/**
 * Every trace in one session. There is no session endpoint, so this lists
 * traces by `session_id` over the longest window the API allows (90 days),
 * independent of the page's time range. Pages arrive newest first.
 */
export function useSessionTracesQuery(sessionId: string) {
  const { projectId } = useProjectParams();
  // Fixed when the page mounts so the query key (and loaded pages) stay stable.
  const [window] = useState(lookbackWindow);
  const query: Omit<TraceListQuery, "cursor"> = {
    from: window.from,
    to: window.to,
    session_id: sessionId,
    limit: SESSION_TRACES_PAGE_SIZE,
  };

  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).traces(query),
    queryFn: ({ pageParam }) => projectsApi.traces(projectId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}
