import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { promptPickerQuery } from "@/features/prompts";
import { useProjectParams } from "@/features/shell";
import {
  playgroundApi,
  pricesApi,
  promptsApi,
  queryKeys,
  type PlaygroundConfig,
  type PromptListItem,
  type PromptVersion,
  type VersionCreate,
} from "@/lib/api";

export { useCredentialsQuery } from "@/features/gateway";

/** The project's prompts, most recently updated first, for the pickers. */
export function usePromptsQuery() {
  const { projectId } = useProjectParams();
  return useQuery({
    ...promptPickerQuery(projectId),
    select: (page): PromptListItem[] => page.items,
  });
}

/** One version of a prompt, shared with the prompt screens' cache. */
export function usePromptVersionFetcher() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return (name: string, version: number): Promise<PromptVersion> =>
    queryClient.query({
      queryKey: queryKeys.project(projectId).prompts.version(name, version),
      queryFn: () => promptsApi.version(projectId, name, version),
      staleTime: 60_000,
    });
}

/** A prompt with its latest version, fresh from the server (for the config a save carries on). */
export function usePromptFetcher() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return (name: string) =>
    queryClient.query({
      queryKey: queryKeys.project(projectId).prompts.detail(name),
      queryFn: () => promptsApi.get(projectId, name),
      staleTime: 0,
    });
}

/** The shared price table, whose model patterns suggest what to type in the model field. */
export function usePricesQuery() {
  return useQuery({
    queryKey: queryKeys.prices,
    queryFn: () => pricesApi.list(),
    staleTime: 10 * 60_000,
  });
}

/** Runs the configurations as one request; an idempotency key keeps a retry from spending twice. */
export function useRunPlayground() {
  const { projectId } = useProjectParams();
  return useMutation({
    mutationFn: ({ configs, key }: { configs: PlaygroundConfig[]; key: string }) =>
      playgroundApi.run(projectId, configs, key),
  });
}

/** Saves a column's messages as a new version of a chat prompt. */
export function useSaveVersion() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, input }: { name: string; input: VersionCreate }) =>
      promptsApi.createVersion(projectId, name, input),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).prompts.all }),
  });
}
