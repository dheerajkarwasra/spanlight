import type { Budget, GatewayKey } from "@/lib/api";

import { BUDGET_COLUMNS } from "./budget-columns";
import { BudgetRow } from "./budget-row";

interface BudgetListProps {
  budgets: readonly Budget[];
  /** The project's gateway keys, to name `gateway_key` scopes; undefined while unknown. */
  gatewayKeys: readonly GatewayKey[] | undefined;
  projectName: string | null;
  canWrite: boolean;
  readOnlyId: string;
}

/** The scope's display name; null for a gateway key that no longer exists. */
function scopeName(
  budget: Budget,
  keys: readonly GatewayKey[] | undefined,
  projectName: string | null,
): string | null {
  switch (budget.scope) {
    case "project":
      return projectName ?? "This project";
    case "gateway_key":
      // While the keys are unknown, the id stands in; a key missing from a loaded list is gone.
      return keys
        ? (keys.find((key) => key.id === budget.scope_id)?.name ?? null)
        : budget.scope_id;
    case "user":
    case "model":
    case "metadata":
    case "trace_name":
      return budget.scope_id;
  }
}

/** Figma "Budgets — List": tiles under overline column labels, in the server's order. */
export function BudgetList({
  budgets,
  gatewayKeys,
  projectName,
  canWrite,
  readOnlyId,
}: BudgetListProps) {
  return (
    <div className="@container flex flex-col gap-1.5">
      <div
        aria-hidden
        className="hidden items-center gap-3 pt-2 pr-3 pb-0.5 pl-4 text-overline text-subtle-foreground uppercase @[56rem]:flex"
      >
        <span className={BUDGET_COLUMNS.name}>Budget</span>
        <span className={BUDGET_COLUMNS.scope}>Scope</span>
        <span className={BUDGET_COLUMNS.period}>Period</span>
        <span className={BUDGET_COLUMNS.spend}>Spend</span>
        <span className={BUDGET_COLUMNS.resets}>Resets</span>
      </div>
      <ul aria-label="Budgets" className="flex flex-col gap-1.5">
        {budgets.map((budget) => (
          <BudgetRow
            key={budget.id}
            budget={budget}
            scopeName={scopeName(budget, gatewayKeys, projectName)}
            canWrite={canWrite}
            readOnlyId={readOnlyId}
          />
        ))}
      </ul>
    </div>
  );
}
