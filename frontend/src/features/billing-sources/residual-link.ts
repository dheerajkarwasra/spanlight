import type { ReconciliationGroup } from "@/lib/api";

const DAY_MS = 86_400_000;

export interface ResidualTracesLink {
  /** The traces list's search: the whole UTC day and the group's first Spanlight model. */
  search: { range: "custom"; from: string; to: string; model: string };
  /** The group's other Spanlight models, which the link does not filter by. */
  extraModels: number;
}

/**
 * Where a model group's residual leads (spec ruling 7): the day's traces of its first Spanlight
 * model. The trace list is per project and can't filter by provider, so there is no link when
 * this project isn't in the report, or when the group has no Spanlight model (an `unmapped:`
 * provider line).
 */
export function residualTracesSearch(
  day: string,
  group: ReconciliationGroup,
  projectIds: string[],
  projectId: string,
): ResidualTracesLink | null {
  const [model, ...others] = group.spanlight_models;
  if (model === undefined || !projectIds.includes(projectId)) {
    return null;
  }
  const next = new Date(Date.parse(`${day}T00:00:00Z`) + DAY_MS).toISOString().slice(0, 10);
  return {
    search: { range: "custom", from: `${day}T00:00Z`, to: `${next}T00:00Z`, model },
    extraModels: others.length,
  };
}
