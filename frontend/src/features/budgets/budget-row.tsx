import { RowAction } from "@/components/tile-list";
import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import { UnknownValue } from "@/components/unknown-value";
import type { Budget } from "@/lib/api";
import { cn } from "@/lib/utils";

import { BudgetDialog } from "./budget-dialog";
import { softLimitBadgeTooltip } from "./budget-enforcement";
import {
  ACTION_LABELS,
  isBlocking,
  NOT_EVALUATED_REASON,
  PERIOD_LABELS,
  resetsInLabel,
  SCOPE_LABELS,
} from "./budget-period";
import { BudgetProgress } from "./budget-progress";
import { BUDGET_COLUMNS } from "./budget-columns";
import { BudgetRowMenu } from "./budget-row-menu";

interface BudgetRowProps {
  budget: Budget;
  /** What the scope is called: the project or gateway key name, the user ID or the model. */
  scopeName: string | null;
  canWrite: boolean;
  readOnlyId: string;
}

/**
 * Figma "Budgets/Budget row" (normal, blocking, unknown) and its 375 px tile: name with the
 * "Blocking (soft)" badge (Figma "Budgets / Soft-limit label", its tooltip on hover and focus), scope, period, spend, reset, Edit and more. The tile stacks the same parts.
 */
export function BudgetRow({ budget, scopeName, canWrite, readOnlyId }: BudgetRowProps) {
  const blocking = isBlocking(budget);
  const mono = budget.scope !== "project";
  const describedBy = canWrite ? undefined : readOnlyId;
  const scope = scopeName ?? <UnknownValue reason="This gateway key no longer exists." />;

  return (
    <li className="flex flex-col gap-3 rounded-tile bg-surface-muted p-4 @[56rem]:flex-row @[56rem]:items-center @[56rem]:py-2.5 @[56rem]:pr-3 @[56rem]:pl-4">
      <div className={cn("flex flex-col gap-0.5", BUDGET_COLUMNS.name)}>
        <div className="flex min-w-0 items-center gap-2">
          <p className="truncate text-sm font-semibold text-foreground" title={budget.name}>
            {budget.name}
          </p>
          {blocking ? (
            <Tooltip content={softLimitBadgeTooltip()} caret="start" side="bottom">
              <Badge variant="danger" tabIndex={0}>
                Blocking (soft)
              </Badge>
            </Tooltip>
          ) : null}
        </div>
        <p className="hidden text-xs font-medium text-muted-foreground @[56rem]:block">
          {ACTION_LABELS[budget.action]}
        </p>
      </div>
      {/* Narrow tile: one line of scope, kind and period. */}
      <p className="flex min-w-0 flex-wrap items-baseline gap-x-1 text-xs text-muted-foreground @[56rem]:hidden">
        <span className={cn("truncate text-sm text-foreground", mono && "font-mono text-[13px]")}>
          {scope}
        </span>
        <span>
          · {SCOPE_LABELS[budget.scope]} · {PERIOD_LABELS[budget.period]}
        </span>
      </p>
      <div className={cn("hidden flex-col gap-0.5 @[56rem]:flex", BUDGET_COLUMNS.scope)}>
        <p
          className={cn("truncate text-foreground", mono ? "font-mono text-[13px]" : "text-[13px]")}
        >
          {scope}
        </p>
        <p className="text-xs font-medium text-muted-foreground">{SCOPE_LABELS[budget.scope]}</p>
      </div>
      <p
        className={cn("hidden text-sm text-muted-foreground @[56rem]:block", BUDGET_COLUMNS.period)}
      >
        {PERIOD_LABELS[budget.period]}
      </p>
      <div className={BUDGET_COLUMNS.spend}>
        <BudgetProgress budget={budget} />
      </div>
      <p className={cn("text-[13px] text-muted-foreground", BUDGET_COLUMNS.resets)}>
        {budget.state === null ? (
          <UnknownValue reason={NOT_EVALUATED_REASON} />
        ) : (
          (resetsInLabel(budget.state.resets_at) ?? (
            <UnknownValue reason="Unreadable reset time." />
          ))
        )}
      </p>
      <div className="flex items-center gap-1.5 @[56rem]:flex-1 @[56rem]:justify-end">
        <BudgetDialog
          budget={budget}
          trigger={
            <RowAction
              disabled={!canWrite}
              aria-label={`Edit ${budget.name}`}
              aria-describedby={describedBy}
              className="@max-[56rem]:h-11 @max-[56rem]:flex-1"
            >
              Edit
            </RowAction>
          }
        />
        <BudgetRowMenu budget={budget} canWrite={canWrite} readOnlyId={readOnlyId} />
      </div>
    </li>
  );
}
