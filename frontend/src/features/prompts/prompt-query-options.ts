import { queryOptions, useQuery } from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell";
import { promptsApi, queryKeys } from "@/lib/api";

/*
 * The prompt reads other features share: the pickers (playground, experiments, evaluators) and
 * a version looked up by id (judges, experiment targets, the trace screens). One key and one
 * limit each, so two screens never cache different shapes or pages under the same entry.
 */

/** More prompts and versions than a picker lists in practice; past this it would need search. */
export const PROMPT_PICKER_LIMIT = 200;

/** The project's prompts, most recently updated first, as one plain page. */
export function promptPickerQuery(projectId: string) {
  return queryOptions({
    queryKey: queryKeys.project(projectId).prompts.picker(PROMPT_PICKER_LIMIT),
    queryFn: () => promptsApi.list(projectId, { limit: PROMPT_PICKER_LIMIT }),
    staleTime: 60_000,
  });
}

/** One prompt's versions, newest first, as one plain page. */
export function promptVersionsPickerQuery(projectId: string, name: string) {
  return queryOptions({
    queryKey: queryKeys.project(projectId).prompts.versionsPicker(name, PROMPT_PICKER_LIMIT),
    queryFn: () => promptsApi.versions(projectId, name, { limit: PROMPT_PICKER_LIMIT }),
    staleTime: 60_000,
  });
}

/** One version by its id, with its prompt's name. Versions never change. */
export function promptVersionByIdQuery(projectId: string, versionId: string) {
  return queryOptions({
    queryKey: queryKeys.project(projectId).prompts.versionById(versionId),
    queryFn: () => promptsApi.versionById(projectId, versionId),
    staleTime: 5 * 60_000,
  });
}

/** The version `versionId` names; off while it is null or empty. */
export function usePromptVersionByIdQuery(versionId: string | null) {
  const { projectId } = useProjectParams();
  return useQuery({
    ...promptVersionByIdQuery(projectId, versionId ?? ""),
    enabled: versionId !== null && versionId !== "",
  });
}
