import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell";
import { alertsApi, queryKeys, type AlertRule } from "@/lib/api";

/*
 * Reads and writes for the rules list and the rule detail page. The evaluator runs every minute,
 * so the state on screen refreshes on the same beat.
 */

const REFRESH_MS = 30_000;
const EVENTS_PAGE_SIZE = 50;
/** Open events across the project, for the list's "Acknowledged" badges (one per rule at most). */
const OPEN_EVENTS_LIMIT = 200;

/** Firing rules first, then by name; budget rules are not listed. */
export function useAlertRulesQuery() {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).alertRules,
    queryFn: () => alertsApi.rules(projectId),
    refetchInterval: REFRESH_MS,
  });
}

export function useAlertRuleQuery(ruleId: string) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).alertRule(ruleId),
    queryFn: () => alertsApi.rule(projectId, ruleId),
    // A rule that never loaded (deleted elsewhere) is not polled; "Try again" refetches. A
    // loaded rule keeps polling through a failed refresh.
    refetchInterval: (query) => (query.state.data === undefined ? false : REFRESH_MS),
  });
}

/** The org's channels, to name and badge the channel ids a rule sends to. */
export function useAlertChannelsQuery() {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).alertChannels,
    queryFn: () => alertsApi.channels(orgId),
  });
}

/** Every open event of the project: which firing rules someone already acknowledged. */
export function useOpenEventsQuery() {
  const { projectId } = useProjectParams();
  const query = { state: "firing", limit: OPEN_EVENTS_LIMIT } as const;
  return useQuery({
    queryKey: queryKeys.project(projectId).alertEvents(query),
    queryFn: () => alertsApi.events(projectId, query),
    select: (page) => page.items,
    refetchInterval: REFRESH_MS,
  });
}

/** One rule's events, newest first, a page at a time ("Load more"). */
export function useRuleEventsQuery(ruleId: string) {
  const { projectId } = useProjectParams();
  const query = { rule_id: ruleId, limit: EVENTS_PAGE_SIZE };
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).alertEvents(query),
    queryFn: ({ pageParam }) => alertsApi.events(projectId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    refetchInterval: REFRESH_MS,
  });
}

/** Every rule's events across the project, newest first, a page at a time ("Load older"). */
export function useEventHistoryQuery() {
  const { projectId } = useProjectParams();
  const query = { limit: EVENTS_PAGE_SIZE };
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).alertEvents(query),
    queryFn: ({ pageParam }) => alertsApi.events(projectId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    refetchInterval: REFRESH_MS,
  });
}

/** Rules, one rule and their events change together: a mute, an acknowledge or a delete. */
function useInvalidateAlerts() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  const keys = queryKeys.project(projectId);
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.alertRules }),
      queryClient.invalidateQueries({ queryKey: keys.alertEventsAll }),
    ]);
}

interface MuteVariables {
  ruleId: string;
  /** ISO time the mute ends; null unmutes. */
  until: string | null;
}

/** The caller reports errors: the mute dialog inline, the Unmute button with a toast. */
export function useMuteRule() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateAlerts();
  return useMutation({
    mutationFn: ({ ruleId, until }: MuteVariables) => alertsApi.muteRule(projectId, ruleId, until),
    onSuccess: invalidate,
  });
}

/** `409 ALREADY_ACKNOWLEDGED` when someone else got there first; the refetch shows who. */
export function useAcknowledgeEvent() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateAlerts();
  return useMutation({
    mutationFn: (eventId: string) => alertsApi.acknowledgeEvent(projectId, eventId),
    onSettled: invalidate,
  });
}

/**
 * Deleting a rule deletes its events too. `onDeleted` runs first (the detail page leaves for the
 * list), so the deleted rule is never refetched into a 404 while its page is still mounted.
 */
export function useDeleteRule(onDeleted?: () => Promise<void>) {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  const keys = queryKeys.project(projectId);
  return useMutation({
    mutationFn: (ruleId: string) => alertsApi.deleteRule(projectId, ruleId),
    onSuccess: async (_result, ruleId) => {
      // Out of the cached list first, so the list never flashes the deleted rule.
      queryClient.setQueryData<AlertRule[]>(keys.alertRules, (rules) =>
        rules?.filter((rule) => rule.id !== ruleId),
      );
      await onDeleted?.();
      queryClient.removeQueries({ queryKey: keys.alertRule(ruleId), exact: true });
      void queryClient.invalidateQueries({ queryKey: keys.alertRules });
      void queryClient.invalidateQueries({ queryKey: keys.alertEventsAll });
    },
  });
}
