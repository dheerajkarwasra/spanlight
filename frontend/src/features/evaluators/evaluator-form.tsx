import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "@tanstack/react-router";
import { useForm, useWatch } from "react-hook-form";

import { Callout } from "@/components/callout";
import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell";
import type { Evaluator } from "@/lib/api";

import { BasicsFields } from "./basics-fields";
import { evaluatorFormSchema, type EvaluatorFormValues } from "./evaluator-schema";
import { FilterFields } from "./filter-fields";
import { FormSideCard } from "./form-side-card";
import { HeuristicFields } from "./heuristic-fields";
import { JudgeFields } from "./judge-fields";
import { SamplingField } from "./sampling-field";
import { useEvaluatorSubmit } from "./use-evaluator-submit";
import { WebhookFields } from "./webhook-fields";

interface EvaluatorFormProps {
  /** Null to create an evaluator. */
  evaluator: Evaluator | null;
  initial: EvaluatorFormValues;
}

/**
 * Figma "Evaluators — Form" (heuristic, expected-output rule, judge, webhook, missing budget,
 * unpriced model, 375 px): Basics, the kind's card, Sampling, Filters, and a side card that
 * explains what the evaluator will do.
 */
export function EvaluatorForm({ evaluator, initial }: EvaluatorFormProps) {
  const { orgId, projectId } = useProjectParams();
  const form = useForm<EvaluatorFormValues>({
    resolver: zodResolver(evaluatorFormSchema),
    defaultValues: initial,
  });
  const kind = useWatch({ control: form.control, name: "kind" });
  const submit = useEvaluatorSubmit(form, evaluator, initial);

  return (
    <form onSubmit={submit.onSubmit} noValidate className="flex flex-col gap-5">
      {submit.problem ? (
        <Callout tone="danger" role="alert" title={submit.problem.title}>
          {submit.problem.detail}
        </Callout>
      ) : null}
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-5">
          <BasicsFields form={form} editing={evaluator !== null} />
          {kind === "heuristic" ? <HeuristicFields form={form} /> : null}
          {kind === "llm_judge" ? <JudgeFields form={form} /> : null}
          {kind === "webhook" ? <WebhookFields form={form} /> : null}
          <SamplingField form={form} />
          <FilterFields form={form} />
          <div className="flex flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
            {submit.pending ? (
              <Button disabled>Cancel</Button>
            ) : (
              <Button asChild>
                <Link
                  to={
                    evaluator
                      ? "/$orgId/$projectId/evaluators/$evaluatorId"
                      : "/$orgId/$projectId/evaluators"
                  }
                  params={{ orgId, projectId, evaluatorId: evaluator?.id ?? "" }}
                >
                  Cancel
                </Link>
              </Button>
            )}
            <Button type="submit" variant="primary" loading={submit.pending}>
              {evaluator ? "Save changes" : "Create evaluator"}
            </Button>
          </div>
        </div>
        <div className="lg:sticky lg:top-6">
          <FormSideCard form={form} />
        </div>
      </div>
    </form>
  );
}
