import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { UseFormReturn } from "react-hook-form";
import { toast } from "sonner";

import { useProjectParams } from "@/features/shell";
import { errorMessage, isApiError, type Evaluator } from "@/lib/api";
import { applyMappedFieldErrors } from "@/lib/form-errors";

import { useCreateEvaluator, useUpdateEvaluator } from "./evaluator-queries";
import { BUDGET_REQUIRED_COPY, type EvaluatorFormValues } from "./evaluator-schema";
import { evaluatorFieldOf, toEvaluatorCreate, toEvaluatorUpdate } from "./evaluator-values";

export interface FormProblem {
  title: string;
  detail: string;
}

/** Problem codes with a home on the form, and the words the frames put there. */
const FIELD_COPY: Record<string, { field: keyof EvaluatorFormValues; message: string }> = {
  NAME_TAKEN: { field: "name", message: "An evaluator with this name already exists here." },
  BUDGET_REQUIRED: { field: "budget", message: BUDGET_REQUIRED_COPY },
  RULE_NEEDS_EXPECTED_OUTPUT: {
    field: "samplingPercent",
    message: "This rule needs an expected output, which live traces lack. Set sampling to 0.",
  },
  CHANNEL_KIND: { field: "channelId", message: "Pick a webhook channel of this organization." },
  JUDGE_TEMPLATE_VARIABLES: {
    field: "promptVersionId",
    message: "A judge prompt must be a chat prompt using only input, output and expected_output.",
  },
  EVALUATOR_MODEL_UNPRICED: {
    field: "model",
    message: "This model has no price, so a budget cannot be enforced. Pick a priced model.",
  },
};

const UNPRICED_PROBLEM: FormProblem = {
  title: "Can't create this evaluator yet",
  detail: "The judge model has no price, so its monthly budget cannot be enforced.",
};

const MISSING_PROBLEM: FormProblem = {
  title: "Something this evaluator uses is gone",
  detail: "A prompt version or credential no longer exists in this project. Pick it again.",
};

const GONE_PROBLEM: FormProblem = {
  title: "Couldn't save these changes",
  detail:
    "The evaluator was deleted, or a prompt version or credential it uses no longer exists. Go back to the list to check.",
};

/**
 * Saves the evaluator form, then opens the evaluator. Refusals land where the frames show them:
 * on the field, above the form for an unpriced model or a vanished reference, else a toast.
 */
export function useEvaluatorSubmit(
  form: UseFormReturn<EvaluatorFormValues>,
  evaluator: Evaluator | null,
  initial: EvaluatorFormValues,
) {
  const { orgId, projectId } = useProjectParams();
  const navigate = useNavigate();
  const create = useCreateEvaluator();
  const update = useUpdateEvaluator(evaluator?.id ?? "");
  const [problem, setProblem] = useState<FormProblem | null>(null);

  function open(evaluatorId: string) {
    void navigate({
      to: "/$orgId/$projectId/evaluators/$evaluatorId",
      params: { orgId, projectId, evaluatorId },
    });
  }

  function handleError(error: unknown) {
    const code = isApiError(error) ? error.code : null;
    const known = code ? FIELD_COPY[code] : undefined;
    if (known) {
      form.setError(known.field, { type: "server", message: known.message }, { shouldFocus: true });
      if (code === "EVALUATOR_MODEL_UNPRICED") {
        setProblem(
          evaluator ? { ...UNPRICED_PROBLEM, title: "Can't save these changes" } : UNPRICED_PROBLEM,
        );
      }
    } else if (isApiError(error) && error.isNotFound) {
      setProblem(evaluator ? GONE_PROBLEM : MISSING_PROBLEM);
    } else if (!applyMappedFieldErrors(error, form.setError, evaluatorFieldOf)) {
      toast.error(errorMessage(error));
    }
  }

  const onSubmit = form.handleSubmit(async (values) => {
    setProblem(null);
    try {
      if (evaluator === null) {
        const created = await create.mutateAsync(toEvaluatorCreate(values));
        toast.success(`Created evaluator "${created.name}".`);
        open(created.id);
        return;
      }
      const changes = toEvaluatorUpdate(values, initial);
      if (Object.keys(changes).length > 0) {
        const saved = await update.mutateAsync(changes);
        toast.success(`Saved evaluator "${saved.name}".`);
      }
      open(evaluator.id);
    } catch (error) {
      handleError(error);
    }
  });

  return { onSubmit, pending: create.isPending || update.isPending, problem };
}
