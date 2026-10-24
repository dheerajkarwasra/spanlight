import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell";
import { orgsApi, promptsApi, queryKeys, type PromptCreate, type VersionCreate } from "@/lib/api";

/** Rows per page of the prompt list and of a prompt's versions. */
const PAGE_SIZE = 50;

/** Most recently updated prompts first; `q` filters by name. Old rows stay while `q` changes. */
export function usePromptListQuery(q: string) {
  const { projectId } = useProjectParams();
  const query = { q: q === "" ? undefined : q, limit: PAGE_SIZE };
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).prompts.list(query),
    queryFn: ({ pageParam }) => promptsApi.list(projectId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    placeholderData: keepPreviousData,
  });
}

/** One prompt with its latest version and labels. `name` is the real name. */
export function usePromptQuery(name: string) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).prompts.detail(name),
    queryFn: () => promptsApi.get(projectId, name),
  });
}

/** The prompt's versions, newest first, each with its seven days of usage. */
export function usePromptVersionsQuery(name: string) {
  const { projectId } = useProjectParams();
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).prompts.versions(name),
    queryFn: ({ pageParam }) =>
      promptsApi.versions(projectId, name, { limit: PAGE_SIZE, cursor: pageParam, usage: true }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}

/** One version; versions never change, so the cache never goes stale. Off while `version` is null. */
export function usePromptVersionQuery(name: string, version: number | null) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).prompts.version(name, version ?? 0),
    queryFn: () => promptsApi.version(projectId, name, version ?? 0),
    enabled: version !== null,
    staleTime: Infinity,
  });
}

/** Traffic on one version over the last seven days. */
export function useVersionUsageQuery(name: string, version: number) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).prompts.usage(name, version),
    queryFn: () => promptsApi.versionUsage(projectId, name, version),
    staleTime: 60_000,
  });
}

/** The org's members, to show who wrote a version. Same cache entry as the Members page. */
export function useAuthorsQuery() {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).members,
    queryFn: () => orgsApi.members(orgId),
    staleTime: 5 * 60_000,
  });
}

function useInvalidatePrompts() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).prompts.all });
}

export function useCreatePrompt() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidatePrompts();
  return useMutation({
    mutationFn: (input: PromptCreate) => promptsApi.create(projectId, input),
    onSuccess: invalidate,
  });
}

export function useCreateVersion(name: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidatePrompts();
  return useMutation({
    mutationFn: (input: VersionCreate) => promptsApi.createVersion(projectId, name, input),
    onSuccess: invalidate,
  });
}

export function useSetLabel(name: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidatePrompts();
  return useMutation({
    mutationFn: ({ label, version }: { label: string; version: number }) =>
      promptsApi.setLabel(projectId, name, label, version),
    onSuccess: invalidate,
  });
}

export function useDeleteLabel(name: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidatePrompts();
  return useMutation({
    mutationFn: (label: string) => promptsApi.deleteLabel(projectId, name, label),
    onSuccess: invalidate,
  });
}

export function useDeletePrompt(name: string) {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => promptsApi.delete(projectId, name),
    onSuccess: () => {
      const keys = queryKeys.project(projectId).prompts;
      // The detail queries would refetch into a 404; drop them, then refresh the list and the
      // other screens' pickers, which would otherwise still offer the prompt.
      queryClient.removeQueries({ queryKey: keys.detail(name) });
      return queryClient.invalidateQueries({ queryKey: keys.all });
    },
  });
}
