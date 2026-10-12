import { Ban, CircleX } from "lucide-react";

import { Notice } from "@/components/notice";
import { errorMessage, isApiError, type EvaluatorTestResult } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { formatScoreValue, toScoreParts } from "@/lib/score-format";

import { BUDGET_SKIPPED_COPY, formatRunCost, runErrorCopy } from "./run-copy";

interface Meta {
  model: string | null;
  tookMs: number | null;
}

function MetaRow({ cost, model, tookMs }: Meta & { cost: string | null }) {
  const items: [string, string][] = [];
  if (cost !== null) items.push(["Cost", cost]);
  if (model !== null) items.push(["Model", model]);
  const took = formatDuration(tookMs);
  if (took !== null) items.push(["Took", took]);
  return (
    <dl className="flex flex-wrap gap-x-8 gap-y-2">
      {items.map(([label, value]) => (
        <div key={label} className="flex flex-col gap-0.5">
          <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
          <dd className="font-mono text-xs font-semibold text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Figma "Test run dialog — result": the score, the judge's reasoning, cost, model and time. */
export function TestResult({
  result,
  judge,
  booleanScores,
  ...meta
}: Meta & { result: EvaluatorTestResult; judge: boolean; booleanScores: boolean }) {
  const score = result.scores[0];
  return (
    <div role="status" className="flex flex-col gap-4 rounded-tile bg-surface-muted p-4">
      {score ? (
        <div className="flex items-center gap-3">
          <p className="text-[34px] leading-none font-bold tracking-[-0.03em] text-foreground tabular">
            {formatScoreValue(toScoreParts(score), booleanScores ? "boolean" : null)}
          </p>
          <div className="flex flex-col">
            <p className="text-sm font-semibold text-foreground">{score.name}</p>
            {judge ? (
              <p className="text-xs text-muted-foreground">0 to 1, higher is better</p>
            ) : null}
          </div>
        </div>
      ) : (
        <p className="text-sm font-medium text-foreground">The evaluator returned no score.</p>
      )}
      {result.reasoning ? (
        <div className="flex flex-col gap-1">
          <p className="text-overline text-subtle-foreground uppercase">Reasoning</p>
          <p className="text-sm text-foreground">{result.reasoning}</p>
        </div>
      ) : null}
      <MetaRow cost={formatRunCost(result.cost_usd)} {...meta} />
    </div>
  );
}

/**
 * Figma "Test run dialog — failed". A budget refusal is the neutral skipped state (no call was
 * made); a failed call names the reason in words with its code; anything else uses the server's
 * own sentence.
 */
export function TestFailure({ error, ...meta }: Meta & { error: unknown }) {
  if (isApiError(error) && error.code === "EVALUATOR_BUDGET_EXCEEDED") {
    return (
      <Notice icon={Ban} role="status">
        {BUDGET_SKIPPED_COPY} No call was made.
      </Notice>
    );
  }
  const code = isApiError(error) ? error.runError : null;
  const failedCall = isApiError(error) && error.code === "EVALUATOR_FAILED";
  return (
    <div role="alert" className="flex flex-col gap-3 rounded-tile bg-danger-subtle p-4">
      <div className="flex items-start gap-2.5">
        <CircleX aria-hidden className="mt-0.5 size-4 shrink-0 text-danger-text" />
        <div className="flex min-w-0 flex-col gap-1">
          <p className="text-sm font-semibold text-danger-text">
            {failedCall ? runErrorCopy(code ?? "") : errorMessage(error)}
          </p>
          {failedCall ? (
            <p className="text-xs text-danger-text">
              No score was written. If the call reached the model, it was billed and counts against
              the budget.
            </p>
          ) : null}
          {code ? <p className="font-mono text-[11px] text-danger-text">{code}</p> : null}
        </div>
      </div>
      {failedCall ? <MetaRow cost={null} {...meta} /> : null}
    </div>
  );
}
