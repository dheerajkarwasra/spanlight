import { Ban, TriangleAlert } from "lucide-react";

import { Notice } from "@/components/notice";
import type { EvaluatorBudget } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  barShare,
  formatBudgetUsd,
  monthLabel,
  readBudget,
  usedPercent,
  WARNING_COPY,
  type BudgetReading,
} from "./budget-math";
import { BUDGET_SKIPPED_COPY } from "./run-copy";

/** The bar: violet below 80 %, the warning tone from 80 % (the text says so too). */
function MeterBar({ reading, className }: { reading: BudgetReading; className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        "w-full overflow-hidden rounded-full border border-border-strong bg-surface",
        className,
      )}
    >
      <div
        className={cn("h-full", reading.tone === "normal" ? "bg-accent" : "bg-warning")}
        style={{ width: `${barShare(reading.ratio) * 100}%` }}
      />
    </div>
  );
}

/**
 * Figma "Evals/Budget meter", inline variant for the list: "$0.31 of $0.50", the warning icon and
 * the percent from 80 %, and a 6 px bar. Null reading (no budget, unreadable) is the caller's.
 */
export function BudgetMeterInline({ reading }: { reading: BudgetReading }) {
  const warn = reading.tone !== "normal";
  return (
    <div className="flex min-w-0 flex-col gap-[5px]">
      <p className="flex items-center gap-1.5 text-xs leading-snug whitespace-nowrap">
        <span className="text-[13px] font-semibold text-foreground tabular">
          {formatBudgetUsd(reading.spent)}
        </span>
        <span className="font-medium text-muted-foreground tabular">
          of {formatBudgetUsd(reading.budget)}
        </span>
        {warn ? (
          <span className="inline-flex items-center gap-1 font-bold text-warning">
            <TriangleAlert aria-hidden className="size-3.5" />
            <span className="tabular">{usedPercent(reading.ratio)}</span>
            <span className="sr-only">of the monthly budget used</span>
          </span>
        ) : null}
      </p>
      <MeterBar reading={reading} className="h-1.5" />
    </div>
  );
}

/**
 * Figma "Evals/Budget meter", card variant: the month, "$0.31 of $0.50 spent", the bar, spent,
 * reserved and left, and from 80 % a warning line (icon and text, never colour alone).
 */
export function BudgetMeter({ status }: { status: EvaluatorBudget }) {
  const reading = readBudget(status);
  const month = monthLabel(status.month_start);
  if (reading === null) {
    return null;
  }
  const left = Math.max(reading.budget - reading.spent - reading.reserved, 0);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3 text-xs">
        <p className="text-overline text-subtle-foreground uppercase">Monthly budget</p>
        {month ? <p className="font-medium text-muted-foreground">{month}</p> : null}
      </div>
      <p className="flex flex-wrap items-baseline gap-x-1.5">
        <span className="text-[28px] leading-none font-bold tracking-[-0.03em] text-foreground tabular">
          {formatBudgetUsd(reading.spent)}
        </span>
        <span className="text-sm font-medium text-muted-foreground tabular">
          of {formatBudgetUsd(reading.budget)} spent
        </span>
      </p>
      <MeterBar reading={reading} className="h-2" />
      <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-medium text-muted-foreground">
        <div className="flex gap-1">
          <dt>Spent</dt>
          <dd className="tabular">{formatBudgetUsd(reading.spent)}</dd>
        </div>
        <div className="flex gap-1">
          <dt>Reserved</dt>
          <dd className="tabular">{formatBudgetUsd(reading.reserved)}</dd>
        </div>
        <div className="flex gap-1 font-semibold text-foreground">
          <dt className="sr-only">Left</dt>
          <dd className="tabular">{formatBudgetUsd(left)} left</dd>
        </div>
      </dl>
      {reading.tone === "spent" ? (
        <Notice tone="warning" icon={Ban} role="status">
          {BUDGET_SKIPPED_COPY}
        </Notice>
      ) : reading.tone === "warning" ? (
        <Notice tone="warning" role="status">
          {WARNING_COPY}
        </Notice>
      ) : null}
    </div>
  );
}
