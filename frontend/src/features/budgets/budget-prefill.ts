import { z } from "zod";

import { projectSearchSchema } from "@/lib/time-range";

import type { BudgetFormValues } from "./budget-schema";

/**
 * Spec ruling 12: `/budgets?new=1&scope=user&value=acme-corp` opens the create dialog prefilled
 * (metadata also takes `key`). An unreadable param is dropped, never an error page. The router
 * reads `new=1` or `value=42` as numbers, so they are coerced back to text.
 */
export const budgetsSearchSchema = projectSearchSchema.extend({
  new: z.coerce.string().pipe(z.literal("1")).optional().catch(undefined),
  scope: z.enum(["trace_name", "user", "metadata", "model"]).optional().catch(undefined),
  value: z.coerce.string().max(512).optional().catch(undefined),
  key: z.coerce.string().max(64).optional().catch(undefined),
});

export type BudgetsSearch = z.infer<typeof budgetsSearchSchema>;

type PrefillSearch = Pick<BudgetsSearch, "new" | "scope" | "value" | "key">;

/** The dialog's prefill, `{}` for a blank create dialog, or null when the URL asks for none. */
export function prefillFromSearch(search: PrefillSearch): Partial<BudgetFormValues> | null {
  if (search.new !== "1") {
    return null;
  }
  const { scope, value, key } = search;
  if (value === undefined) {
    return {};
  }
  switch (scope) {
    case "trace_name":
      return { scope, traceName: value };
    case "user":
      return { scope, userId: value };
    case "model":
      return { scope, model: value };
    case "metadata":
      return key === undefined ? {} : { scope, metadataKey: key, metadataValue: value };
    case undefined:
      return {};
  }
}
