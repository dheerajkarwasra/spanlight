import { getRouteApi } from "@tanstack/react-router";
import { useId } from "react";

import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { CompareStep, TraceComparison } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";

import { StepRow } from "./compare-step-row";
import { stepChanged, stepState, type StepState } from "./trace-compare";

const route = getRouteApi("/_authed/$orgId/$projectId/traces/compare");

/** Figma "Traces — Compare" step list: one row per step, A → B with the change. */
export function CompareSteps({ comparison }: { comparison: TraceComparison }) {
  const { changed: changedOnly = false } = route.useSearch();
  const navigate = route.useNavigate();
  const switchId = useId();
  const counts = summaryCounts(comparison.steps);
  const steps = changedOnly ? comparison.steps.filter(stepChanged) : comparison.steps;

  return (
    <Card role="region" aria-labelledby="compare-steps" className="flex flex-col gap-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h2 id="compare-steps" className="text-lg font-semibold text-foreground">
            Steps
          </h2>
          <p className="text-xs text-muted-foreground" aria-live="polite">
            {[pluralize(comparison.steps.length, "step"), ...counts].join(" · ")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Switch
            id={switchId}
            checked={changedOnly}
            onCheckedChange={(next) =>
              void navigate({
                search: (prev) => ({ ...prev, changed: next ? true : undefined }),
                replace: true,
              })
            }
          />
          <Label htmlFor={switchId} className="text-sm">
            Changed steps only
          </Label>
        </div>
      </div>
      {steps.length === 0 ? (
        <p className="rounded-tile bg-surface-muted p-4 text-sm text-muted-foreground">
          {comparison.steps.length === 0
            ? "Neither trace has spans."
            : "Every step has the same output, input, model, tokens and cost. Turn off the filter to see their timings."}
        </p>
      ) : (
        <>
          <ol aria-label="Steps" className="flex flex-col gap-2.5">
            {steps.map((step) => (
              <StepRow
                key={`${step.name}#${step.occurrence}`}
                step={step}
                traces={{ a: comparison.a.trace_id, b: comparison.b.trace_id }}
              />
            ))}
          </ol>
          <p className="text-xs text-muted-foreground">
            A step only in one trace shows its values on that side. Each A or B link opens the step
            in its trace.
          </p>
        </>
      )}
    </Card>
  );
}

const SUMMARY: [StepState[], string][] = [
  [["output_changed"], "changed"],
  [["same_output"], "same"],
  [["only_a"], "only in A"],
  [["only_b"], "only in B"],
  [["output_unknown"], "unknown"],
  [["failed_a", "failed_b"], "failed"],
];

/** "2 changed", "1 only in B", … for the states that occur. */
function summaryCounts(steps: readonly CompareStep[]): string[] {
  const states = steps.map(stepState);
  return SUMMARY.flatMap(([members, word]) => {
    const count = states.filter((state) => members.includes(state)).length;
    return count > 0 ? [`${formatInteger(count)} ${word}`] : [];
  });
}
