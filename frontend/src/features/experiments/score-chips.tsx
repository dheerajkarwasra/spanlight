import {
  ArrowDown,
  ArrowUp,
  Bot,
  Info,
  ListChecks,
  Minus,
  Shuffle,
  Webhook,
  type LucideIcon,
} from "lucide-react";

import type { EvaluatorKind } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { ItemChange } from "./compare-view";

const KINDS: Record<EvaluatorKind, { label: string; icon: LucideIcon }> = {
  heuristic: { label: "Heuristic", icon: ListChecks },
  llm_judge: { label: "LLM judge", icon: Bot },
  webhook: { label: "Webhook", icon: Webhook },
};

const OUTLINE =
  "inline-flex min-h-[26px] shrink-0 items-center gap-1.5 rounded-full border border-border-strong bg-surface px-2.5 py-1 text-xs font-semibold whitespace-nowrap";

/** Figma "Evals/Kind chip": which kind of evaluator produced a score. */
export function KindChip({ kind }: { kind: EvaluatorKind }) {
  const { label, icon: Icon } = KINDS[kind];
  return (
    <span className={cn(OUTLINE, "text-muted-foreground")}>
      <Icon aria-hidden className="size-3.5" />
      {label}
    </span>
  );
}

/** Figma "Few items" chip: an interval over fewer than ten items is wide; it says so in words. */
export function FewItemsChip() {
  return (
    <span className={cn(OUTLINE, "text-muted-foreground")}>
      <Info aria-hidden className="size-3.5" />
      few items
    </span>
  );
}

/** One item's score in an outlined pill ("0.95"). */
export function ScorePill({ children, label }: { children: string; label?: string }) {
  return (
    <span className={cn(OUTLINE, "px-2.5 py-0.5 font-bold text-foreground")} aria-label={label}>
      {children}
    </span>
  );
}

const CHANGES: Record<ItemChange, { label: string; icon: LucideIcon; classes: string }> = {
  improved: { label: "Improved", icon: ArrowUp, classes: "bg-success-subtle text-success" },
  regressed: { label: "Regressed", icon: ArrowDown, classes: "bg-danger-subtle text-danger-text" },
  unchanged: { label: "Unchanged", icon: Minus, classes: "bg-surface-muted text-muted-foreground" },
  changed: { label: "Changed", icon: Shuffle, classes: "bg-surface-muted text-muted-foreground" },
  not_comparable: {
    label: "Not comparable",
    icon: Minus,
    classes: "bg-surface-muted text-muted-foreground",
  },
};

/** Figma "Evals/Change pill": improved or regressed always carry an icon and a word. */
export function ChangePill({ change, className }: { change: ItemChange; className?: string }) {
  const { label, icon: Icon, classes } = CHANGES[change];
  return (
    <span
      className={cn(
        "inline-flex min-h-[26px] shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap",
        classes,
        className,
      )}
    >
      <Icon aria-hidden className="size-3.5" strokeWidth={2.5} />
      {label}
    </span>
  );
}
