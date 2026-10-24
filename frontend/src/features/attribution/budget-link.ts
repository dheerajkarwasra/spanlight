import type { BudgetsSearch } from "@/features/budgets";
import type { AttributionGroupBy } from "@/lib/api";

/**
 * Spec ruling 12: the `/budgets` search that opens the create dialog prefilled for one Spend
 * group, or null when budgets can't scope it (a release, or metadata without its key).
 */
export function budgetSearchFor(
  groupBy: AttributionGroupBy,
  key: string | null,
  value: string,
): BudgetsSearch | null {
  switch (groupBy) {
    case "trace_name":
    case "user":
      return { new: "1", scope: groupBy, value };
    case "metadata":
      return key === null ? null : { new: "1", scope: "metadata", key, value };
    case "release":
      return null;
  }
}
