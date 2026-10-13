import { Check } from "lucide-react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";
import { useId } from "react";

import { Notice } from "@/components/notice";
import { Skeleton } from "@/components/ui/skeleton";
import type { Evaluator, EvaluatorBudget } from "@/lib/api";
import { formatCost } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useEvaluatorBudgets, useEvaluatorsQuery } from "./experiment-queries";
import { LoadFailed } from "./load-failed";
import { KindChip } from "./score-chips";

interface EvaluatorPickerProps {
  selected: string[];
  onChange: (ids: string[]) => void;
}

/** The line under an evaluator's name: what it is and what it costs. */
function evaluatorNote(evaluator: Evaluator, budget: EvaluatorBudget | undefined): string {
  const off = evaluator.enabled ? "" : " · turned off, runs skip it";
  if (evaluator.config.kind === "llm_judge") {
    const left = formatCost(budget?.remaining_usd ?? null);
    return `Judge · ${left === null ? "monthly budget" : `${left} left this month`}${off}`;
  }
  if (evaluator.config.kind === "webhook") {
    return `Webhook · your endpoint scores each item${off}`;
  }
  const rule = evaluator.config.check.rule;
  const expected = rule === "exact_match" || rule === "levenshtein";
  return `Rule · ${expected ? "compares with each item's expected output" : "free"}${off}`;
}

/** The Evaluators card: tick the evaluators that score every item. */
export function EvaluatorPicker({ selected, onChange }: EvaluatorPickerProps) {
  const evaluators = useEvaluatorsQuery();
  const judges = (evaluators.data ?? []).filter((item) => item.config.kind === "llm_judge");
  const budgets = useEvaluatorBudgets(judges.map((judge) => judge.id));

  if (evaluators.isPending) {
    return <Skeleton className="h-[136px] w-full rounded-tile" />;
  }
  if (evaluators.isError) {
    return <LoadFailed what="evaluators" onRetry={() => void evaluators.refetch()} />;
  }
  if (evaluators.data.length === 0) {
    return (
      <Notice>
        No evaluators yet. The run still stores every answer, its cost and latency; add an evaluator
        under Evaluators to score them.
      </Notice>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      {evaluators.data.map((evaluator) => (
        <EvaluatorOption
          key={evaluator.id}
          evaluator={evaluator}
          note={evaluatorNote(evaluator, budgets.get(evaluator.id))}
          checked={selected.includes(evaluator.id)}
          onCheckedChange={(checked) =>
            onChange(
              checked ? [...selected, evaluator.id] : selected.filter((id) => id !== evaluator.id),
            )
          }
        />
      ))}
    </div>
  );
}

interface EvaluatorOptionProps {
  evaluator: Evaluator;
  note: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

function EvaluatorOption({ evaluator, note, checked, onCheckedChange }: EvaluatorOptionProps) {
  const noteId = useId();
  return (
    <label
      className={cn(
        "flex cursor-pointer items-center gap-3 rounded-tile px-4 py-3 transition-colors",
        checked ? "bg-surface-selected" : "bg-surface-muted hover:bg-surface-hover",
      )}
    >
      <CheckboxPrimitive.Root
        checked={checked}
        onCheckedChange={(next) => onCheckedChange(next === true)}
        aria-describedby={noteId}
        className="flex size-5 shrink-0 items-center justify-center rounded-checkbox border-[1.5px] border-subtle-foreground bg-surface data-[state=checked]:border-ink data-[state=checked]:bg-ink"
      >
        <CheckboxPrimitive.Indicator>
          <Check aria-hidden className="size-3.5 text-ink-foreground" strokeWidth={3} />
        </CheckboxPrimitive.Indicator>
      </CheckboxPrimitive.Root>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-[15px] font-semibold text-foreground">{evaluator.name}</span>
        <span id={noteId} className="text-xs font-medium text-muted-foreground">
          {note}
        </span>
      </span>
      <KindChip kind={evaluator.config.kind} />
    </label>
  );
}
