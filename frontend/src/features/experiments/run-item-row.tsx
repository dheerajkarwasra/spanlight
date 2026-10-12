import { CircleX } from "lucide-react";

import { CostValue, UNPRICED_OR_NO_USAGE_REASON } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import type { ExperimentItem } from "@/lib/api";
import { cn } from "@/lib/utils";

import { formatLatency } from "./compare-view";
import type { DatasetItemRef } from "./experiment-queries";
import { inputText, itemLabel, itemProblem } from "./item-copy";
import { ItemScore } from "./item-score";
import type { ScoreMeta } from "./score-meta";

interface ItemRowProps {
  item: ExperimentItem;
  scores: readonly ScoreMeta[];
  input: DatasetItemRef | undefined;
}

/** One item of a run: its input, output or failure, scores, latency and cost. */
export function ItemRow({ item, scores, input }: ItemRowProps) {
  const problem = item.error ? itemProblem(item.error) : null;
  return (
    <li
      className={cn(
        "grid grid-cols-2 items-center gap-3 rounded-tile px-4 py-3.5 text-sm sm:grid-cols-4 lg:grid-cols-[var(--cols)]",
        problem ? "bg-danger-subtle" : "bg-surface-muted",
      )}
    >
      <div className="col-span-2 flex min-w-0 flex-col gap-0.5 sm:col-span-4 lg:col-span-1">
        <span className="font-mono text-xs text-accent">
          {itemLabel(item.dataset_item_id, input?.position)}
        </span>
        <span className="line-clamp-2 text-foreground">
          {inputText(input?.input) ?? <UnknownValue reason="The dataset item could not be read." />}
        </span>
      </div>
      <div className="col-span-2 min-w-0 sm:col-span-4 lg:col-span-1">
        {problem ? (
          <div className="flex flex-col gap-0.5">
            <span className="flex items-start gap-1.5 text-danger-text">
              <CircleX aria-hidden className="mt-0.5 size-3.5 shrink-0" />
              {problem.text}
            </span>
            {problem.code ? (
              <span className="pl-5 font-mono text-[11px] text-muted-foreground">
                {problem.code}
              </span>
            ) : null}
          </div>
        ) : (
          <span className="line-clamp-3 text-muted-foreground">{item.output?.text ?? ""}</span>
        )}
      </div>
      {scores.map((meta) => (
        <span key={meta.name} className="flex flex-col gap-0.5">
          <span className="text-overline text-subtle-foreground uppercase lg:sr-only">
            {meta.name}
          </span>
          {problem ? null : (
            <ItemScore
              meta={meta}
              value={item.scores[meta.name]}
              evaluatorError={item.evaluator_error}
            />
          )}
        </span>
      ))}
      <span className="flex flex-col gap-0.5 font-mono text-xs text-foreground">
        <span className="font-sans text-overline text-subtle-foreground uppercase lg:sr-only">
          Latency
        </span>
        {formatLatency(item.latency_ms) ?? (
          <UnknownValue reason="Unknown: the call was never sent." />
        )}
      </span>
      <span className="flex flex-col gap-0.5 font-mono text-xs text-foreground">
        <span className="font-sans text-overline text-subtle-foreground uppercase lg:sr-only">
          Cost
        </span>
        <CostValue cost={item.cost_usd} unknownReason={UNPRICED_OR_NO_USAGE_REASON} />
      </span>
    </li>
  );
}
