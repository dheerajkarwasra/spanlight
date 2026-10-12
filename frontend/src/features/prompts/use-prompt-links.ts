import { getRouteApi } from "@tanstack/react-router";

import { useProjectParams } from "@/features/shell";
import { fromPathPromptName, toPathPromptName } from "@/lib/api";

import type { PromptDetailSearch } from "./search";

const detailRoute = getRouteApi("/_authed/$orgId/$projectId/prompts/$name");

type DetailView = Pick<PromptDetailSearch, "view" | "v" | "a" | "b">;

/** Link props for one screen of a prompt; time range and environment are kept by the router. */
export function usePromptLink(name: string, view: DetailView = {}) {
  const { orgId, projectId } = useProjectParams();
  return {
    to: "/$orgId/$projectId/prompts/$name" as const,
    params: { orgId, projectId, name: toPathPromptName(name) },
    search: view,
  };
}

/** The real name of the prompt the current detail route shows. */
export function usePromptName(): string {
  return fromPathPromptName(detailRoute.useParams().name);
}

/** The diff screen between two versions of the current prompt. */
export function useDiffLink(from: number, to: number) {
  return usePromptLink(usePromptName(), { view: "diff", a: from, b: to });
}
