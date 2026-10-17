import { useState } from "react";
import type { UseFormReturn } from "react-hook-form";
import { toast } from "sonner";

import { errorMessage, isApiError, type Budget } from "@/lib/api";
import { applyMappedFieldErrors } from "@/lib/form-errors";

import {
  budgetFieldOf,
  scopeField,
  toBudgetInput,
  toBudgetUpdate,
  type BudgetFormValues,
} from "./budget-schema";
import { useCreateBudget, useUpdateBudget } from "./budgets-queries";

/**
 * Saves the budget form and maps every refusal to where the frames show it:
 * `BUDGET_NAME_TAKEN` on Name, `UNKNOWN_SCOPE` on the scope's field (the gateway key, the metadata
 * key or the trace name), `UNKNOWN_CHANNEL` on Channels,
 * `LIMIT_EXCEEDED` above the form, other 422 field errors on their fields.
 */
export function useBudgetSubmit(
  form: UseFormReturn<BudgetFormValues>,
  budget: Budget | null,
  projectName: string,
  onSaved: () => void,
) {
  const createBudget = useCreateBudget();
  const updateBudget = useUpdateBudget();
  const [problem, setProblem] = useState<string | null>(null);

  function handleError(error: unknown, values: BudgetFormValues) {
    const code = isApiError(error) ? error.code : null;
    const scopeTarget = scopeField(values.scope);
    if (code === "BUDGET_NAME_TAKEN") {
      form.setError("name", {
        type: "server",
        message: `A budget with this name already exists in ${projectName}.`,
      });
    } else if (code === "UNKNOWN_SCOPE" && scopeTarget) {
      form.setError(scopeTarget, {
        type: "server",
        message:
          values.scope === "gateway_key"
            ? "This gateway key isn't in this project any more. Pick another."
            : errorMessage(error),
      });
    } else if (code === "UNKNOWN_CHANNEL") {
      form.setError("channelIds", {
        type: "server",
        message: "A channel was deleted meanwhile. Remove it and save again.",
      });
    } else if (code === "LIMIT_EXCEEDED") {
      setProblem(errorMessage(error));
    } else if (
      !applyMappedFieldErrors(error, form.setError, (path) => budgetFieldOf(path, values.scope))
    ) {
      toast.error(errorMessage(error));
    }
  }

  const onSubmit = form.handleSubmit(async (values) => {
    setProblem(null);
    try {
      if (budget === null) {
        const created = await createBudget.mutateAsync(toBudgetInput(values));
        toast.success(`Created budget "${created.name}".`);
      } else {
        const update = toBudgetUpdate(values, budget);
        if (Object.keys(update).length > 0) {
          const saved = await updateBudget.mutateAsync({ budgetId: budget.id, update });
          toast.success(`Saved budget "${saved.name}".`);
        }
      }
      onSaved();
    } catch (error) {
      handleError(error, values);
    }
  });

  return { onSubmit, pending: createBudget.isPending || updateBudget.isPending, problem };
}
