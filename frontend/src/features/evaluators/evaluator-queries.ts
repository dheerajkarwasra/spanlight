import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { promptPickerQuery, promptVersionsPickerQuery } from "@/features/prompts";
import { useProjectParams } from "@/features/shell";
import {
  evaluatorsApi,
  pricesApi,
  projectsApi,
  queryKeys,
  type AgreementSummary,
  type Evaluator,
  type EvaluatorCreate,
  type EvaluatorRunStatus,
  type EvaluatorUpdate,
  type PromptListItem,
} from "@/lib/api";

export { useAlertChannelsQuery } from "@/features/alerts";
export { useCredentialsQuery } from "@/features/gateway";
export { usePromptVersionByIdQuery } from "@/features/prompts";

/*
 * Reads and writes for the evaluator screens. Sampling runs every minute, so runs and budgets
 * poll at that pace.
 */

const REFRESH_MS = 60_000;
const LIST_PAGE = 100;
/** Where judge prompts live once a built-in template is materialized (spec §8.5). */
export const JUDGE_PROMPT_PREFIX = "judges/";

/** Newest first, with "Load more". Each judge carries its month's budget, so it polls. */
export function useEvaluatorsQuery() {
  const { projectId } = useProjectParams();
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).evaluators.list,
    queryFn: ({ pageParam }) =>
      evaluatorsApi.list(projectId, { limit: LIST_PAGE, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    select: (data): Evaluator[] => data.pages.flatMap((page) => page.items),
    refetchInterval: REFRESH_MS,
  });
}

export function useEvaluatorQuery(evaluatorId: string) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).evaluators.detail(evaluatorId),
    queryFn: () => evaluatorsApi.get(projectId, evaluatorId),
  });
}

export function useEvaluatorBudgetQuery(evaluatorId: string, enabled = true) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).evaluators.budget(evaluatorId),
    queryFn: () => evaluatorsApi.budget(projectId, evaluatorId),
    enabled,
    refetchInterval: REFRESH_MS,
  });
}

/** Judge versus reviewer scores on the same traces, computed on read. */
export function useEvaluatorAgreement(evaluatorId: string) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).evaluators.agreement(evaluatorId),
    queryFn: () => evaluatorsApi.agreement(projectId, evaluatorId),
  });
}

/** Every judge's agreement in one request, for the badges beside judge means. */
export function useEvaluatorAgreementSummary() {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).evaluators.agreementSummary,
    queryFn: () => evaluatorsApi.agreementSummary(projectId),
    select: (data) => data.items,
  });
}

/** The judge whose score is `scoreName` (scores are named after evaluators); undefined if none. */
export function agreementForScore(
  summary: readonly AgreementSummary[] | undefined,
  scoreName: string,
): AgreementSummary | undefined {
  return summary?.find((item) => item.score_name === scoreName);
}

export function useEvaluatorRunsQuery(evaluatorId: string, status: EvaluatorRunStatus | undefined) {
  const { projectId } = useProjectParams();
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).evaluators.runs(evaluatorId, { status }),
    queryFn: ({ pageParam }) =>
      evaluatorsApi.runs(projectId, evaluatorId, { status, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    placeholderData: keepPreviousData,
    refetchInterval: REFRESH_MS,
  });
}

function useInvalidateEvaluators() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).evaluators.all });
}

/** A judge saved from a built-in template materializes `judges/<name>` as a new prompt. */
function materializesPrompt(input: EvaluatorCreate | EvaluatorUpdate): boolean {
  const config: unknown = input.config;
  return typeof config === "object" && config !== null && "judge_template" in config;
}

function useRefreshAfterSave() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  const invalidate = useInvalidateEvaluators();
  return (input: EvaluatorCreate | EvaluatorUpdate) =>
    Promise.all([
      invalidate(),
      materializesPrompt(input)
        ? queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).prompts.all })
        : undefined,
    ]);
}

export function useCreateEvaluator() {
  const { projectId } = useProjectParams();
  const refresh = useRefreshAfterSave();
  return useMutation({
    mutationFn: (input: EvaluatorCreate) => evaluatorsApi.create(projectId, input),
    onSuccess: (_saved, input) => refresh(input),
  });
}

export function useUpdateEvaluator(evaluatorId: string) {
  const { projectId } = useProjectParams();
  const refresh = useRefreshAfterSave();
  return useMutation({
    mutationFn: (update: EvaluatorUpdate) => evaluatorsApi.update(projectId, evaluatorId, update),
    onSuccess: (_saved, update) => refresh(update),
  });
}

/** A test run writes a `test` run and may spend: the runs and the budget refresh after it. */
export function useTestEvaluator(evaluatorId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateEvaluators();
  return useMutation({
    mutationFn: (traceId: string) => evaluatorsApi.test(projectId, evaluatorId, traceId),
    onSettled: invalidate,
  });
}

/** Chat prompts a judge can use, the `judges/*` templates first. */
export function useJudgePromptsQuery() {
  const { projectId } = useProjectParams();
  return useQuery({
    ...promptPickerQuery(projectId),
    select: (page): PromptListItem[] => judgePromptsFirst(page.items),
  });
}

export function judgePromptsFirst(items: readonly PromptListItem[]): PromptListItem[] {
  const chat = items.filter((item) => item.kind === "chat");
  const isJudge = (item: PromptListItem) => item.name.startsWith(JUDGE_PROMPT_PREFIX);
  return [...chat.filter(isJudge), ...chat.filter((item) => !isJudge(item))];
}

/** One prompt's versions for the judge's Version select (its own plain page, not the paged list). */
export function usePromptVersionsQuery(name: string) {
  const { projectId } = useProjectParams();
  return useQuery({
    ...promptVersionsPickerQuery(projectId, name),
    select: (page) => page.items,
    enabled: name !== "",
  });
}

/** Environments, models and tags the project's traces carry. Same cache entry as the shell's. */
export function useFilterSuggestionsQuery() {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).filters,
    queryFn: () => projectsApi.filters(projectId),
    staleTime: 5 * 60_000,
  });
}

/** The shared price table: model suggestions for the judge. */
export function usePricesQuery() {
  return useQuery({
    queryKey: queryKeys.prices,
    queryFn: () => pricesApi.list(),
    staleTime: 10 * 60_000,
  });
}

const TEST_TRACE_DAYS = 30;
const TEST_TRACE_SHOWN = 5;
/** Fetched to leave room for the internal traces dropped below. */
const TEST_TRACE_FETCH = 25;
/** Spanlight's own traces, which online sampling never scores either (spec §8.6). */
const INTERNAL_ENVIRONMENTS = new Set(["evals", "playground", "experiments", "doctor"]);
const JUDGE_TAG = "spanlight:judge";

/** Recent traces to test on, newest first, without Spanlight's own; `q` searches ids and names. */
export function useTestTracesQuery(q: string) {
  const { projectId } = useProjectParams();
  const search = q.trim();
  return useQuery({
    queryKey: [...queryKeys.project(projectId).evaluators.all, "test-traces", search] as const,
    queryFn: () => {
      const to = new Date();
      const from = new Date(to.getTime() - TEST_TRACE_DAYS * 24 * 60 * 60_000);
      return projectsApi.traces(projectId, {
        from: from.toISOString(),
        to: to.toISOString(),
        q: search === "" ? undefined : search,
        limit: TEST_TRACE_FETCH,
      });
    },
    select: (page) =>
      page.items
        .filter(
          (trace) =>
            !INTERNAL_ENVIRONMENTS.has(trace.environment ?? "") && !trace.tags.includes(JUDGE_TAG),
        )
        .slice(0, TEST_TRACE_SHOWN),
    placeholderData: keepPreviousData,
  });
}
