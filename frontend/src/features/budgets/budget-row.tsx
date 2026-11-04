import { ChevronDown } from "lucide-react";
import { useId, useState } from "react";

import { RowAction } from "@/components/tile-list";
import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import { UnknownValue } from "@/components/unknown-value";
import type { Budget } from "@/lib/api";
import { cn } from "@/lib/utils";

import { BudgetBurn } from "./budget-burn";
import { BudgetDialog } from "./budget-dialog";
import { blockingBadge } from "./budget-enforcement";
import {
  ACTION_LABELS,
  isBlocking,
  NOT_EVALUATED_REASON,
  PERIOD_LABELS,
  resetsInLabel,
  SCOPE_LABELS,
} from "./budget-period";
import { BudgetProgress } from "./budget-progress";
import { BUDGET_GRID } from "./budget-columns";
import { BudgetRowMenu } from "./budget-row-menu";

interface BudgetRowProps {
  budget: Budget;
  /** What the scope is called: the project or gateway key name, the user ID or the model. */
  scopeName: string | null;
  canWrite: boolean;
}

/**
 * Figma "Budgets/Budget row" (normal, blocking, unknown) and its 375 px tile: name with the
 * blocking badge ("Blocking" for a gateway key, "Blocking (soft)" where SDK spend counts; Figma
 * "Budgets — Hard admission (G-007)", its tooltip on hover and focus), scope, period, spend,
 * reset, Show burn, Edit and more. The tile stacks the same parts; the burn panel opens under them.
 */
export function BudgetRow({ budget, scopeName, canWrite }: BudgetRowProps) {
  const badge = isBlocking(budget) ? blockingBadge(budget.scope) : null;
  const mono = budget.scope !== "project";
  const scope = scopeName ?? <UnknownValue reason="This gateway key no longer exists." />;
  const [showBurn, setShowBurn] = useState(false);
  const burnId = useId();

  return (
    <li className="flex flex-col gap-3 rounded-tile bg-surface-muted p-4 @[64rem]:py-2.5 @[64rem]:pr-3 @[64rem]:pl-4">
      <div className={cn("flex flex-col gap-3", BUDGET_GRID)}>
        <div className="flex min-w-0 flex-col gap-0.5">
          <div className="flex min-w-0 items-center gap-2">
            <p className="truncate text-sm font-semibold text-foreground" title={budget.name}>
              {budget.name}
            </p>
            {badge ? (
              <Tooltip content={badge.tooltip} caret="start" side="bottom">
                <Badge variant="danger" tabIndex={0}>
                  {badge.label}
                </Badge>
              </Tooltip>
            ) : null}
          </div>
          <p className="hidden text-xs font-medium text-muted-foreground @[64rem]:block">
            {ACTION_LABELS[budget.action]}
          </p>
        </div>
        {/* Narrow tile: one line of scope, kind and period. */}
        <p className="flex min-w-0 flex-wrap items-baseline gap-x-1 text-xs text-muted-foreground @[64rem]:hidden">
          <span className={cn("truncate text-sm text-foreground", mono && "font-mono text-[13px]")}>
            {scope}
          </span>
          <span>
            · {SCOPE_LABELS[budget.scope]} · {PERIOD_LABELS[budget.period]}
          </span>
        </p>
        <div className="hidden min-w-0 flex-col gap-0.5 @[64rem]:flex">
          <p
            className={cn(
              "truncate text-foreground",
              mono ? "font-mono text-[13px]" : "text-[13px]",
            )}
          >
            {scope}
          </p>
          <p className="text-xs font-medium text-muted-foreground">{SCOPE_LABELS[budget.scope]}</p>
        </div>
        <p className="hidden text-sm text-muted-foreground @[64rem]:block">
          {PERIOD_LABELS[budget.period]}
        </p>
        <div className="min-w-0">
          <BudgetProgress budget={budget} />
        </div>
        <p className="text-[13px] text-muted-foreground">
          {budget.state === null ? (
            <UnknownValue reason={NOT_EVALUATED_REASON} />
          ) : (
            (resetsInLabel(budget.state.resets_at) ?? (
              <UnknownValue reason="Unreadable reset time." />
            ))
          )}
        </p>
        <div className="flex items-center gap-1.5 @[64rem]:justify-end">
          <RowAction
            aria-expanded={showBurn}
            aria-controls={burnId}
            className="@max-[64rem]:h-11 @max-[64rem]:flex-1"
            onClick={() => {
              setShowBurn(!showBurn);
            }}
          >
            {showBurn ? "Hide burn" : "Show burn"}
            <ChevronDown
              aria-hidden
              className={cn("transition-transform", showBurn && "rotate-180")}
            />
          </RowAction>
          {canWrite ? (
            <>
              <BudgetDialog
                budget={budget}
                trigger={
                  <RowAction
                    aria-label={`Edit ${budget.name}`}
                    className="@max-[64rem]:h-11 @max-[64rem]:flex-1"
                  >
                    Edit
                  </RowAction>
                }
              />
              <BudgetRowMenu budget={budget} />
            </>
          ) : null}
        </div>
      </div>
      {showBurn ? <BudgetBurn budgetId={budget.id} id={burnId} /> : null}
    </li>
  );
}
