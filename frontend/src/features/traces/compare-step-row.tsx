import { Link } from "@tanstack/react-router";
import { ArrowRight, ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { DeltaPill } from "@/components/delta-pill";
import { UnknownValue } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatLatency } from "@/features/experiments/compare-view";
import { useProjectParams } from "@/features/shell/project-context";
import type { CompareStep, StepSpan } from "@/lib/api";
import { formatCost, formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { CompareStepDiff } from "./compare-step-diff";
import { SpanKindIcon } from "./span-kind";
import { stepState, type StepState } from "./trace-compare";

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

/** One step, A → B, with a "Show diff" disclosure of its input and output. */
export function StepRow({ step, traces }: { step: CompareStep; traces: { a: string; b: string } }) {
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
  const [diffOpen, setDiffOpen] = useState(false);
  const diffId = useId();

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
        <Button
          size="sm"
          variant="ghost"
          aria-label={`${diffOpen ? "Hide" : "Show"} diff of ${step.name}`}
          aria-expanded={diffOpen}
          aria-controls={diffOpen ? diffId : undefined}
          onClick={() => {
            setDiffOpen((open) => !open);
          }}
        >
          <ChevronDown
            aria-hidden
            className={cn("transition-transform", diffOpen && "rotate-180")}
          />
          {diffOpen ? "Hide diff" : "Show diff"}
        </Button>
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
      {diffOpen ? (
        <div id={diffId} className="col-span-full">
          <CompareStepDiff
            traceIdA={traces.a}
            traceIdB={traces.b}
            spanA={step.a ?? undefined}
            spanB={step.b ?? undefined}
          />
        </div>
      ) : null}
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
