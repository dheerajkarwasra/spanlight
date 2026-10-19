import { getRouteApi, Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import { useId, type ReactNode } from "react";

import { DeltaPill } from "@/components/delta-pill";
import { UnknownValue } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { formatLatency } from "@/features/experiments/compare-view";
import { useProjectParams } from "@/features/shell/project-context";
import type { CompareStep, StepSpan, TraceComparison } from "@/lib/api";
import { formatCost, formatInteger, pluralize } from "@/lib/format";

import { SpanKindIcon } from "./span-kind";
import { stepChanged, stepState, type StepState } from "./trace-compare";

const route = getRouteApi("/_authed/$orgId/$projectId/traces/compare");

const STATE_BADGES: Record<
  StepState,
  { label: string; variant: "neutral" | "warning" | "danger" | "outline" }
> = {
  only_a: { label: "Only in A", variant: "outline" },
  only_b: { label: "Only in B", variant: "outline" },
  failed_a: { label: "Failed in A", variant: "danger" },
  failed_b: { label: "Failed in B", variant: "danger" },
  output_changed: { label: "Output changed", variant: "warning" },
  same_output: { label: "Same output", variant: "neutral" },
  output_unknown: { label: "Output unknown", variant: "neutral" },
};

const ROW_GRID = "lg:grid-cols-[minmax(0,1.5fr)_9rem_repeat(3,minmax(0,1fr))]";

const NOT_IN_TRACE = "Unknown: the step is not in this trace.";
const NOT_REPORTED = "Unknown: not reported for this step.";

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

function StepRow({ step, traces }: { step: CompareStep; traces: { a: string; b: string } }) {
  const state = stepState(step);
  const badge = STATE_BADGES[state];
  const kind = step.a?.kind ?? step.b?.kind ?? "other";
  const modelChanged = step.a && step.b && step.a.model !== step.b.model;
  const tokens = (side: StepSpan | null) =>
    side && side.input_tokens !== null && side.output_tokens !== null
      ? side.input_tokens + side.output_tokens
      : null;
  const tokenDelta =
    step.input_tokens === null || step.output_tokens === null
      ? null
      : step.input_tokens + step.output_tokens;

  return (
    <li
      className={`grid grid-cols-1 gap-3 rounded-tile border border-border bg-surface p-4 lg:items-center ${ROW_GRID}`}
    >
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 items-center gap-2">
          <SpanKindIcon kind={kind} />
          <span className="truncate text-sm font-medium text-foreground" title={step.name}>
            {step.name}
          </span>
          {step.occurrence > 1 ? (
            <span className="font-mono text-xs text-subtle-foreground">#{step.occurrence}</span>
          ) : null}
        </div>
        {modelChanged ? (
          <p className="truncate pl-6 font-mono text-xs text-muted-foreground">
            {step.a?.model ?? "no model"} → {step.b?.model ?? "no model"}
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-1.5 lg:flex-col lg:items-start">
        <Badge variant={badge.variant}>{badge.label}</Badge>
        {step.input_changed ? <Badge variant="outline">Input changed</Badge> : null}
        <span className="flex gap-1">
          <SpanLink trace={traces.a} side={step.a} label="A" />
          <SpanLink trace={traces.b} side={step.b} label="B" />
        </span>
      </div>
      <Measure
        label="Duration"
        before={step.a ? formatLatency(step.a.duration_ms) : null}
        after={step.b ? formatLatency(step.b.duration_ms) : null}
        step={step}
        delta={step.duration_ms}
        format={(ms) => formatLatency(ms) ?? ""}
      />
      <Measure
        label="Tokens"
        before={formatInteger(tokens(step.a))}
        after={formatInteger(tokens(step.b))}
        step={step}
        delta={tokenDelta}
        format={(count) => formatInteger(count) ?? ""}
      />
      <Measure
        label="Cost"
        before={formatCost(step.a?.cost_usd)}
        after={formatCost(step.b?.cost_usd)}
        step={step}
        delta={step.cost_usd === null ? null : Number(step.cost_usd)}
        format={(usd) => formatCost(usd) ?? ""}
      />
    </li>
  );
}

function SpanLink({ trace, side, label }: { trace: string; side: StepSpan | null; label: string }) {
  const { orgId, projectId } = useProjectParams();
  if (side === null) {
    return null;
  }
  return (
    <Link
      to="/$orgId/$projectId/traces/$traceId"
      params={{ orgId, projectId, traceId: trace }}
      search={{ span: side.span_id }}
      aria-label={`Open this step in trace ${label}`}
      className="inline-flex min-h-6 min-w-6 items-center justify-center rounded-full border border-border px-2 text-2xs font-semibold text-muted-foreground transition-colors hover:border-foreground hover:text-foreground"
    >
      {label}
    </Link>
  );
}

interface MeasureProps {
  label: string;
  before: string | null;
  after: string | null;
  step: CompareStep;
  /** B minus A; null when either side is unknown. */
  delta: number | null;
  format: (absolute: number) => string;
}

function Measure({ label, before, after, step, delta, format }: MeasureProps) {
  const value = (text: string | null, present: boolean): ReactNode =>
    text ?? <UnknownValue reason={present ? NOT_REPORTED : NOT_IN_TRACE} />;
  return (
    <div className="flex min-w-0 items-center gap-2 lg:flex-col lg:items-start lg:gap-1">
      <span className="w-16 shrink-0 text-overline text-subtle-foreground uppercase lg:w-auto">
        {label}
      </span>
      {before === null && after === null ? (
        // Neither side has the value (a step that reports no tokens, say): one dash, not two.
        <UnknownValue reason={NOT_REPORTED} className="font-mono text-xs" />
      ) : (
        <span className="flex flex-wrap items-center gap-x-1.5 gap-y-1 font-mono text-xs">
          <span className="text-muted-foreground">{value(before, step.a !== null)}</span>
          <ArrowRight aria-hidden className="size-3 text-subtle-foreground" />
          <span className="sr-only">to</span>
          <span className="font-semibold text-foreground">{value(after, step.b !== null)}</span>
        </span>
      )}
      <DeltaPill delta={delta} increaseIsGood={false} format={format} showArrow />
    </div>
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
