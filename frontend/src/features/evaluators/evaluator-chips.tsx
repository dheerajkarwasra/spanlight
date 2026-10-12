import { Bot, ListChecks, Webhook, type LucideIcon } from "lucide-react";

import type { EvaluatorKind } from "@/lib/api";
import { cn } from "@/lib/utils";

import { KIND_LABELS } from "./evaluator-labels";

const KIND_ICONS: Record<EvaluatorKind, LucideIcon> = {
  heuristic: ListChecks,
  llm_judge: Bot,
  webhook: Webhook,
};

const PILL =
  "inline-flex min-h-[26px] shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap";

/** Figma "Evals/Kind chip": an outlined pill with the kind's icon. */
export function KindChip({ kind }: { kind: EvaluatorKind }) {
  const Icon = KIND_ICONS[kind];
  return (
    <span className={cn(PILL, "border border-border-strong bg-surface text-muted-foreground")}>
      <Icon aria-hidden className="size-3.5" />
      {KIND_LABELS[kind]}
    </span>
  );
}

/** Figma "Evals/State pill": Enabled (success, dot) or Disabled (outlined, grey dot). */
export function StatePill({ enabled }: { enabled: boolean }) {
  return (
    <span
      className={cn(
        PILL,
        enabled
          ? "bg-success-subtle text-success"
          : "border border-border-strong bg-surface text-muted-foreground",
      )}
    >
      <span
        aria-hidden
        className={cn("size-[7px] rounded-full", enabled ? "bg-success" : "bg-subtle-foreground")}
      />
      {enabled ? "Enabled" : "Disabled"}
    </span>
  );
}
