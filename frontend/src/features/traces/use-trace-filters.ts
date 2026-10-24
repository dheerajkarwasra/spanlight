import { getRouteApi } from "@tanstack/react-router";
import { useCallback } from "react";

import { usePromptVersionByIdQuery } from "@/features/prompts";
import { CLEARED_SCORE_FILTER, scoreFilterText, useScoreTypeOf } from "@/features/scores";
import type { ScoreDataType } from "@/lib/api";
import { errorClassLabel, isErrorClass } from "@/lib/error-class";
import { formatPromptVersion } from "@/lib/format";

import { metadataFilterLabel } from "./metadata-filter";
import type { TraceFilters } from "./search";

const tracesRoute = getRouteApi("/_authed/$orgId/$projectId/traces");

/** The facet filters owned by the traces page. */
export const FACET_KEYS = [
  "status",
  "error_class",
  "model",
  "release",
  "tag",
  "user",
  "session",
  "prompt_version",
  "name",
  "q",
] as const;
export type FacetKey = (typeof FACET_KEYS)[number];

/**
 * Every filter that shows up as a removable chip. `env` belongs to the shell (it also scopes the
 * overview and sessions), but it narrows this list too, so it gets a chip here.
 */
export const FILTER_KEYS = ["env", ...FACET_KEYS] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];

export const FILTER_LABELS: Record<FilterKey, string> = {
  env: "Environment",
  status: "Status",
  error_class: "Error class",
  model: "Model",
  release: "Release",
  tag: "Tag",
  user: "User",
  session: "Session",
  prompt_version: "Prompt version",
  name: "Trace name",
  q: "Search",
};

/** The score facet's URL params, and "has scores". They show as one chip each. */
type ScoreKey = "score" | "score_min" | "score_max" | "score_value" | "has_scores";

/** "Has retries" (a chip) and the list's sort and direction (no chip: they filter nothing). */
type RetryKey = "has_retries" | "sort" | "order";

/** The metadata pair's URL params; they show as one chip. */
type MetadataParam = "metadata_key" | "metadata_value";

export type FilterPatch = Partial<
  Pick<TraceFilters, FilterKey | ScoreKey | RetryKey | MetadataParam>
>;

/** A prompt version id is a UUID; its chip shows the first block, like a short commit hash. */
const SHORT_ID_LENGTH = 8;

/**
 * A chip's key: a facet, the whole score filter (`score`), `has_scores`, `has_retries` or the
 * metadata pair (`metadata`).
 */
export type ActiveFilterKey = FilterKey | "score" | "has_scores" | "has_retries" | "metadata";

export interface ActiveFilter {
  key: ActiveFilterKey;
  label: string;
  value: string;
  /** The metadata chip's key and value; no value is the "without this key" filter. */
  pair?: { key: string; value: string | undefined };
}

const STATUS_LABELS: Record<NonNullable<TraceFilters["status"]>, string> = {
  ok: "OK",
  error: "Error",
};

/**
 * The removable chips for the filters in the URL. `scoreTypeOf` turns a boolean score's 1/0 into
 * Yes/No in the score chip; `promptVersionText` ("support/answer v3") names the prompt version
 * once it is looked up, in place of its short id.
 */
export function activeFiltersOf(
  search: TraceFilters,
  scoreTypeOf: (name: string) => ScoreDataType | null | undefined = () => null,
  promptVersionText: string | null = null,
): ActiveFilter[] {
  const active: ActiveFilter[] = [];
  for (const key of FILTER_KEYS) {
    const raw = search[key];
    if (raw === undefined || raw === "") {
      continue;
    }
    const isStatus = key === "status" && (raw === "ok" || raw === "error");
    let value = raw;
    if (isStatus) {
      value = STATUS_LABELS[raw];
    } else if (key === "error_class" && isErrorClass(raw)) {
      value = errorClassLabel(raw);
    } else if (key === "prompt_version") {
      value = promptVersionText ?? raw.slice(0, SHORT_ID_LENGTH);
    }
    active.push({ key, label: FILTER_LABELS[key], value });
  }
  if (search.metadata_key !== undefined) {
    const pair = { key: search.metadata_key, value: search.metadata_value };
    active.push({
      key: "metadata",
      label: "Metadata",
      value: metadataFilterLabel(pair.key, pair.value),
      pair,
    });
  }
  const score = scoreFilterText(search, search.score ? (scoreTypeOf(search.score) ?? null) : null);
  if (score !== null) {
    active.push({ key: "score", label: "Score", value: score });
  } else if (hasOrphanScoreParams(search)) {
    // A range or value without a name (an edited or old URL) is ignored by the list query; the
    // chip still shows so it can be removed.
    active.push({ key: "score", label: "Score", value: "no score name, ignored" });
  }
  if (search.has_scores !== undefined) {
    active.push({
      key: "has_scores",
      label: "Scores",
      value: search.has_scores ? "has scores" : "no scores",
    });
  }
  if (search.has_retries === true) {
    active.push({ key: "has_retries", label: "Retries", value: "has retries" });
  }
  return active;
}

/** True when "has retries" is the only filter narrowing the list (the environment aside). */
export function onlyRetriesFilter(active: readonly ActiveFilter[]): boolean {
  return (
    active.some((filter) => filter.key === "has_retries") &&
    active.every((filter) => filter.key === "has_retries" || filter.key === "env")
  );
}

function hasOrphanScoreParams(search: TraceFilters): boolean {
  return (
    search.score_min !== undefined ||
    search.score_max !== undefined ||
    search.score_value !== undefined
  );
}

/** What removing a chip changes: the score and metadata chips clear their whole filter. */
export function removalPatch(key: ActiveFilterKey): FilterPatch {
  if (key === "score") {
    return { ...CLEARED_SCORE_FILTER };
  }
  if (key === "metadata") {
    return { metadata_key: undefined, metadata_value: undefined };
  }
  const patch: FilterPatch = {};
  patch[key] = undefined;
  return patch;
}

/**
 * Blank facet values clear their filter. No metadata key clears the value too; an empty value is
 * the "without this key" filter. A value is matched as stored, so it is never trimmed.
 */
export function normaliseFilterPatch(patch: FilterPatch): FilterPatch {
  const normalised: FilterPatch = { ...patch };
  for (const key of FILTER_KEYS) {
    const value = normalised[key];
    if (value !== undefined && value.trim() === "") {
      normalised[key] = undefined;
    }
  }
  if ("metadata_key" in normalised && !normalised.metadata_key?.trim()) {
    normalised.metadata_key = undefined;
    normalised.metadata_value = undefined;
  }
  if (normalised.metadata_value === "") {
    normalised.metadata_value = undefined;
  }
  return normalised;
}

function clearedFilters(): Record<FilterKey | ScoreKey | MetadataParam | "has_retries", undefined> {
  return {
    ...CLEARED_SCORE_FILTER,
    metadata_key: undefined,
    metadata_value: undefined,
    name: undefined,
    has_scores: undefined,
    has_retries: undefined,
    env: undefined,
    status: undefined,
    error_class: undefined,
    model: undefined,
    release: undefined,
    tag: undefined,
    user: undefined,
    session: undefined,
    prompt_version: undefined,
    q: undefined,
  };
}

/** URL-synced filters for the traces list. */
export function useTraceFilters() {
  const search = tracesRoute.useSearch();
  const navigate = tracesRoute.useNavigate();
  const scoreTypeOf = useScoreTypeOf();

  /** Applies several filter changes in one navigation; blank values clear a filter. */
  const setFilters = useCallback(
    (patch: FilterPatch) => {
      const normalised = normaliseFilterPatch(patch);
      void navigate({
        search: (prev) => ({ ...prev, ...normalised }),
        // Typing in the search box shouldn't flood the history stack.
        replace: Object.keys(patch).every((key) => key === "q"),
      });
    },
    [navigate],
  );

  const setFilter = useCallback(
    <K extends FilterKey>(key: K, value: TraceFilters[K]) => {
      const patch: FilterPatch = {};
      patch[key] = value;
      setFilters(patch);
    },
    [setFilters],
  );

  const clearFilters = useCallback(() => {
    void navigate({ search: (prev) => ({ ...prev, ...clearedFilters() }) });
  }, [navigate]);

  const promptVersion = usePromptVersionByIdQuery(search.prompt_version ?? null).data;
  const activeFilters = activeFiltersOf(
    search,
    scoreTypeOf,
    promptVersion ? formatPromptVersion(promptVersion.prompt_name, promptVersion.version) : null,
  );

  return {
    search,
    activeFilters,
    /** Any chip is showing, including the environment. */
    hasActiveFilters: activeFilters.length > 0,
    /** A facet narrows the list below the window totals (the environment doesn't). */
    hasFacetFilters: activeFilters.some((filter) => filter.key !== "env"),
    setFilter,
    setFilters,
    clearFilters,
  };
}
