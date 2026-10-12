import { getRouteApi } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { errorMessage } from "@/lib/api";

import { pickVersion, type ColumnState } from "./config-state";
import { usePromptVersionFetcher } from "./playground-queries";

const playgroundRoute = getRouteApi("/_authed/$orgId/$projectId/playground");

/**
 * "Open in playground" on a prompt lands here with `?prompt=&v=`: once, load that version into
 * the first column. Returns why it could not be loaded, or null.
 */
export function useSeedColumn(
  setColumns: (update: (columns: ColumnState[]) => ColumnState[]) => void,
): string | null {
  const { prompt, v } = playgroundRoute.useSearch();
  const fetchVersion = usePromptVersionFetcher();
  const seeded = useRef(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (seeded.current || prompt === undefined || v === undefined) {
      return;
    }
    seeded.current = true;
    fetchVersion(prompt, v).then(
      (version) =>
        setColumns((columns) =>
          columns.map((column, index) =>
            index === 0 ? { ...column, ...pickVersion(column, prompt, version) } : column,
          ),
        ),
      (error: unknown) =>
        setProblem(`Couldn't open ${prompt} v${String(v)}: ${errorMessage(error)}`),
    );
  }, [prompt, v, fetchVersion, setColumns]);

  return problem;
}
