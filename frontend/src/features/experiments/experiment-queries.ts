import {
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell";
import {
  datasetsApi,
  evaluatorsApi,
  experimentsApi,
  pricesApi,
  queryKeys,
  type ExperimentCreate,
  type ExperimentRun,
} from "@/lib/api";

import { isActive } from "./run-label";

export { useCredentialsQuery } from "@/features/gateway";

export { useScoreConfigsQuery } from "@/features/scores";

/** A running experiment moves a few items a second; the screens follow it on this beat. */
const POLL_MS = 3_000;
const LIST_PAGE = 50;
/** The API's largest page: one read lists every run of an experiment in practice. */
const MAX_PAGE = 200;
/** An experiment reads at most this many dataset items (`EXPERIMENT_MAX_ITEMS` default). */
const MAX_DATASET_ITEMS = 1_000;

function experimentKeys(projectId: string) {
  return queryKeys.project(projectId).experiments;
}

/** The project's experiments, newest first, each with its latest run; refreshed while one runs. */
export function useExperimentsQuery() {
  const { projectId } = useProjectParams();
  return useInfiniteQuery({
    queryKey: experimentKeys(projectId).list,
    queryFn: ({ pageParam }) =>
      experimentsApi.list(projectId, { limit: LIST_PAGE, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) =>
        page.items.some((experiment) => experiment.last_run && isActive(experiment.last_run)),
      )
        ? POLL_MS
        : false,
  });
}

export function useExperimentQuery(experimentId: string) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: experimentKeys(projectId).detail(experimentId),
    queryFn: () => experimentsApi.get(projectId, experimentId),
  });
}

/** Every run of an experiment (newest first), refreshed while one is queued or running. */
export function useRunsQuery(experimentId: string, enabled = true) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: experimentKeys(projectId).runs(experimentId),
    queryFn: () => experimentsApi.runs(projectId, experimentId, { limit: MAX_PAGE }),
    select: (page) => page.items,
    enabled,
    refetchInterval: (query) =>
      query.state.data?.items.some((run) => isActive(run)) ? POLL_MS : false,
  });
}

function runQueryOptions(projectId: string, experimentId: string, runId: string) {
  return {
    queryKey: experimentKeys(projectId).run(experimentId, runId),
    queryFn: () => experimentsApi.run(projectId, experimentId, runId),
    refetchInterval: (query: { state: { data?: ExperimentRun | undefined } }) =>
      query.state.data && isActive(query.state.data) ? POLL_MS : false,
  } as const;
}

/** One run with its totals. */
export function useRunQuery(experimentId: string, runId: string | undefined) {
  const { projectId } = useProjectParams();
  return useQuery({
    ...runQueryOptions(projectId, experimentId, runId ?? ""),
    enabled: runId !== undefined,
  });
}

/** A run's items in dataset order, a page at a time. */
export function useRunItemsQuery(experimentId: string, run: ExperimentRun | undefined) {
  const { projectId } = useProjectParams();
  const runId = run?.id ?? "";
  return useInfiniteQuery({
    queryKey: experimentKeys(projectId).items(experimentId, runId),
    queryFn: ({ pageParam }) =>
      experimentsApi.items(projectId, experimentId, runId, { limit: MAX_PAGE, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    enabled: run !== undefined,
    refetchInterval: run && isActive(run) ? POLL_MS : false,
  });
}

/**
 * Run `b` against run `a`. `ready` is true once both run reads show the runs ended: the API
 * refuses a run that is still queued or running, and the polled run reads flip `ready` when it
 * ends, so the comparison loads then without a reload.
 */
export function useCompareQuery(
  experimentId: string,
  a: string | undefined,
  b: string | undefined,
  ready: boolean,
) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: experimentKeys(projectId).compare(experimentId, a ?? "", b ?? ""),
    queryFn: () => experimentsApi.compare(projectId, experimentId, a ?? "", b ?? ""),
    enabled: ready && a !== undefined && b !== undefined && a !== b,
  });
}

export function useCreateExperiment() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ExperimentCreate) => experimentsApi.create(projectId, input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: experimentKeys(projectId).list }),
  });
}

/** Queues a run; the key makes a retried click start one run, not two. */
export function useStartRun(experimentId: string) {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key, promptVersionId }: { key: string; promptVersionId?: string | null }) =>
      experimentsApi.startRun(projectId, experimentId, key, promptVersionId),
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: experimentKeys(projectId).runs(experimentId) }),
        queryClient.invalidateQueries({ queryKey: experimentKeys(projectId).list }),
      ]),
  });
}

/** The project's datasets, for the form and to name an experiment's dataset. */
export function useDatasetsQuery() {
  const { projectId } = useProjectParams();
  return useQuery({
    // A plain page, not the datasets screen's paged list: its own key so the shapes never mix.
    queryKey: [...queryKeys.project(projectId).datasets.all, "picker"],
    queryFn: () => datasetsApi.list(projectId, { limit: MAX_PAGE }),
    select: (page) => page.items,
  });
}

export function useDatasetQuery(datasetId: string | undefined) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).datasets.detail(datasetId ?? ""),
    queryFn: () => datasetsApi.get(projectId, datasetId ?? ""),
    enabled: datasetId !== undefined,
  });
}

/** A dataset item's input and its 1-based position in dataset order, by item id. */
export interface DatasetItemRef {
  position: number;
  input: unknown;
}

/** Every item input of a dataset (an experiment reads at most 1,000), to label compared items. */
export function useDatasetInputs(datasetId: string | undefined) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: [
      ...queryKeys.project(projectId).datasets.items(datasetId ?? "", { limit: MAX_PAGE }),
      "all",
    ],
    queryFn: async () => {
      const refs = new Map<string, DatasetItemRef>();
      let cursor: string | null = null;
      do {
        const page = await datasetsApi.items(projectId, datasetId ?? "", {
          limit: MAX_PAGE,
          cursor,
        });
        for (const item of page.items) {
          refs.set(item.id, { position: refs.size + 1, input: item.input });
        }
        cursor = page.next_cursor;
      } while (cursor !== null && refs.size < MAX_DATASET_ITEMS);
      return refs;
    },
    enabled: datasetId !== undefined,
    staleTime: 60_000,
  });
}

/** The project's evaluators: their names are the score names, their kind labels each score. */
export function useEvaluatorsQuery() {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: [...queryKeys.project(projectId).evaluators.all, "picker"],
    queryFn: () => evaluatorsApi.list(projectId, { limit: MAX_PAGE }),
    select: (page) => page.items,
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

/** Each judge's month so far, for "$0.19 left this month" in the evaluator picker. */
export function useEvaluatorBudgets(evaluatorIds: readonly string[]) {
  const { projectId } = useProjectParams();
  return useQueries({
    queries: evaluatorIds.map((evaluatorId) => ({
      queryKey: queryKeys.project(projectId).evaluators.budget(evaluatorId),
      queryFn: () => evaluatorsApi.budget(projectId, evaluatorId),
      staleTime: 30_000,
    })),
    combine: (results) =>
      new Map(
        results.flatMap((result, index) =>
          result.data ? [[evaluatorIds[index] ?? "", result.data] as const] : [],
        ),
      ),
  });
}
