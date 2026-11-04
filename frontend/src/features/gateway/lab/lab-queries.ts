import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useProjectParams } from "@/features/shell";
import {
  gatewayApi,
  projectsApi,
  queryKeys,
  type FaultProfile,
  type FaultProfileCreate,
  type FaultProfileUpdate,
  type TraceListQuery,
  type TraceSummary,
} from "@/lib/api";

import { useFaultProfilesQuery } from "../gateway-queries";

export const LAB_RUNS_LIMIT = 10;
const LAB_WINDOW_DAYS = 7;
const DAY_MS = 86_400_000;

/** The trace tag the gateway puts on a call a fault profile changed. */
export function labTag(scenario: string): string {
  return `lab:${scenario}`;
}

export interface LabRun {
  trace: TraceSummary;
  /** The scenario from the trace's `lab:<scenario>` tag. */
  scenario: string;
}

/**
 * Recent faulted calls. The traces API filters on one exact tag, so this asks once per scenario
 * the project's profiles use (last 7 days, newest few of each), merges the answers and keeps the
 * newest overall. No profiles, no requests. The window is fixed when the page opens, so the query
 * keys stay stable.
 */
export function useLabRunsQuery() {
  const { projectId } = useProjectParams();
  const profiles = useFaultProfilesQuery();
  const [range] = useState(() => {
    const now = Math.floor(Date.now() / 60_000) * 60_000;
    return {
      from: new Date(now - LAB_WINDOW_DAYS * DAY_MS).toISOString(),
      to: new Date(now + 60_000).toISOString(),
    };
  });
  const scenarios = [...new Set((profiles.data ?? []).map((profile) => profile.scenario))];

  const runs = useQueries({
    queries: scenarios.map((scenario) => {
      const query: Omit<TraceListQuery, "cursor"> = {
        ...range,
        tag: labTag(scenario),
        limit: LAB_RUNS_LIMIT,
      };
      return {
        queryKey: queryKeys.project(projectId).traces(query),
        queryFn: ({ signal }) => projectsApi.traces(projectId, query, signal),
      };
    }),
    combine: (results) => {
      const merged: LabRun[] = [];
      results.forEach((result, index) => {
        const scenario = scenarios[index];
        for (const trace of result.data?.items ?? []) {
          if (scenario) {
            merged.push({ trace, scenario });
          }
        }
      });
      merged.sort((a, b) => b.trace.started_at.localeCompare(a.trace.started_at));
      return {
        runs: merged.slice(0, LAB_RUNS_LIMIT),
        isPending: results.some((result) => result.isPending),
        // One scenario failing hides runs that exist, so any failure is an error state.
        error: results.find((result) => result.isError)?.error ?? null,
        refetch: () => {
          results.forEach((result) => {
            void result.refetch();
          });
        },
      };
    },
  });

  return {
    runs: runs.runs,
    isPending: profiles.isPending || runs.isPending,
    error: profiles.error ?? runs.error,
    refetch: () => {
      void profiles.refetch();
      runs.refetch();
    },
  };
}

/** Everything the Lab changes lives under the gateway keys: profiles and keys both refresh. */
function useInvalidateLab() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).gateway.all });
  };
}

export interface SaveProfileInput {
  /** Null creates a profile. */
  profileId: string | null;
  create: FaultProfileCreate;
  /** Used for an edit; `scenario` and `params` always travel together. */
  update: FaultProfileUpdate;
  /** Keys that should run the profile after the save. */
  keyIds: readonly string[];
  /** Keys that ran it before. */
  previousKeyIds: readonly string[];
}

export interface KeyFailure {
  keyId: string;
  error: unknown;
}

export interface SaveProfileResult {
  profile: FaultProfile;
  /** The keys that run the profile now: the earlier ones, changed by every update that worked. */
  attachedKeyIds: string[];
  /** Key updates that failed. The profile itself is saved, and the updates that worked stay. */
  failures: KeyFailure[];
}

/**
 * Saves a profile, then moves the keys: newly ticked keys take the profile (replacing any other),
 * unticked ones lose it. The profile is saved first and the key updates settle one by one, so a
 * failing key never loses the profile or hides the other keys' results. Only a failed profile
 * save throws.
 */
export function useSaveFaultProfile() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateLab();

  return useMutation({
    mutationFn: async (input: SaveProfileInput): Promise<SaveProfileResult> => {
      const profile =
        input.profileId === null
          ? await gatewayApi.createFaultProfile(projectId, input.create)
          : await gatewayApi.updateFaultProfile(projectId, input.profileId, input.update);
      const wanted = new Set(input.keyIds);
      const before = new Set(input.previousKeyIds);
      const attach = input.keyIds.filter((id) => !before.has(id));
      const detach = input.previousKeyIds.filter((id) => !wanted.has(id));
      const changes = [
        ...attach.map((keyId) => ({ keyId, attach: true })),
        ...detach.map((keyId) => ({ keyId, attach: false })),
      ];
      const settled = await Promise.allSettled(
        changes.map((change) =>
          gatewayApi.updateKey(projectId, change.keyId, {
            fault_profile_id: change.attach ? profile.id : null,
          }),
        ),
      );

      const attached = new Set(before);
      const failures: KeyFailure[] = [];
      settled.forEach((result, index) => {
        const change = changes[index];
        if (!change) {
          return;
        }
        if (result.status === "rejected") {
          failures.push({ keyId: change.keyId, error: result.reason });
        } else if (change.attach) {
          attached.add(change.keyId);
        } else {
          attached.delete(change.keyId);
        }
      });
      return { profile, attachedKeyIds: [...attached], failures };
    },
    onSettled: invalidate,
  });
}

/** The row switch: only `enabled` changes. */
export function useToggleFaultProfile() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateLab();
  return useMutation({
    mutationFn: (input: { profileId: string; enabled: boolean }) =>
      gatewayApi.updateFaultProfile(projectId, input.profileId, { enabled: input.enabled }),
    onSettled: invalidate,
  });
}

export function useDeleteFaultProfile() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateLab();
  return useMutation({
    mutationFn: (profileId: string) => gatewayApi.deleteFaultProfile(projectId, profileId),
    onSettled: invalidate,
  });
}
