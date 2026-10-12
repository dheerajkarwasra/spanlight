import { useQueries, useQuery } from "@tanstack/react-query";

import {
  promptPickerQuery,
  promptVersionByIdQuery,
  promptVersionsPickerQuery,
} from "@/features/prompts";
import { useProjectParams } from "@/features/shell";
import type { ExperimentTarget, PromptListItem, PromptVersion } from "@/lib/api";

import type { PromptRef, VersionIndex } from "./run-label";

/** The project's prompts, for the target pickers. */
export function usePromptListQuery() {
  const { projectId } = useProjectParams();
  return useQuery({
    ...promptPickerQuery(projectId),
    select: (page): PromptListItem[] => page.items,
  });
}

/** The versions of one prompt, newest first. */
export function usePromptVersionsQuery(name: string | undefined) {
  const { projectId } = useProjectParams();
  return useQuery({
    ...promptVersionsPickerQuery(projectId, name ?? ""),
    select: (page): PromptVersion[] => page.items,
    enabled: name !== undefined,
  });
}

/** The prompt version ids the targets on screen name (ad-hoc targets name none). */
export function targetVersionIds(targets: readonly (ExperimentTarget | undefined)[]): string[] {
  return [
    ...new Set(
      targets.flatMap((target) =>
        target?.kind === "prompt_version" ? [target.prompt_version_id] : [],
      ),
    ),
  ];
}

/**
 * Prompt version id → prompt name and number, one read per distinct version on screen (shared
 * with every screen that looks a version up by id; versions never change).
 */
export function useVersionIndex(versionIds: readonly string[]): VersionIndex {
  const { projectId } = useProjectParams();
  return useQueries({
    queries: versionIds.map((versionId) => promptVersionByIdQuery(projectId, versionId)),
    combine: (results) => {
      const index = new Map<string, PromptRef>();
      results.forEach((result, at) => {
        const id = versionIds[at];
        if (result.data && id !== undefined) {
          index.set(id, { name: result.data.prompt_name, version: result.data.version });
        }
      });
      return index;
    },
  });
}
