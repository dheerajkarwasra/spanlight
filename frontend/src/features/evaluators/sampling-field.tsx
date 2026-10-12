import { useId } from "react";
import { useWatch, type UseFormReturn } from "react-hook-form";

import { Notice } from "@/components/notice";
import { SectionCard } from "@/components/section-card";
import { cn } from "@/lib/utils";

import { EXPECTED_OUTPUT_RULES, type EvaluatorFormValues } from "./evaluator-schema";

function samplingHint(percent: number, heuristic: boolean): string {
  if (percent <= 0) {
    return "Sampling is off. The evaluator runs only in test runs and experiments.";
  }
  if (percent >= 100) {
    return heuristic
      ? "Every matching trace is scored. Rules are free, so a high rate is fine."
      : "Every matching trace is scored.";
  }
  return `About 1 in ${Math.round(100 / percent)} matching traces.`;
}

/**
 * Figma "Sampling": the share of matching traces scored, chosen by a stable hash. Rules that need
 * an expected output are fixed at 0 with the reason shown.
 */
export function SamplingField({ form }: { form: UseFormReturn<EvaluatorFormValues> }) {
  const [percent, kind, rule] = useWatch({
    control: form.control,
    name: ["samplingPercent", "kind", "rule"],
  });
  const fixed = kind === "heuristic" && EXPECTED_OUTPUT_RULES.has(rule);
  const id = useId();
  const error = form.formState.errors.samplingPercent?.message;
  const shown = `${Number(percent.toFixed(2))}%`;

  function setPercent(next: number) {
    // Two decimals of a percent are the four decimals of the stored rate.
    const clamped = Number.isFinite(next) ? Math.min(Math.max(next, 0), 100) : 0;
    form.setValue("samplingPercent", Math.round(clamped * 100) / 100, {
      shouldDirty: true,
      shouldValidate: form.formState.isSubmitted,
    });
  }

  return (
    <SectionCard
      title="Sampling"
      description="How many matching traces are scored. Chosen by a stable hash, so a trace is scored at most once."
      className="gap-4"
    >
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-3">
          <label htmlFor={id} className="text-sm font-semibold text-foreground">
            Share of traces
          </label>
          {/* Exact entry for rates the slider's whole steps can't hit, e.g. 0.5 %. */}
          <span className="inline-flex items-center rounded-full border border-border-strong pr-2.5 text-xs font-semibold text-foreground">
            <input
              type="number"
              min={0}
              max={100}
              step={0.01}
              value={percent}
              disabled={fixed}
              aria-label="Share of traces, in percent"
              onChange={(event) => setPercent(event.target.valueAsNumber)}
              className="w-14 [appearance:textfield] rounded-full bg-transparent py-0.5 pl-2.5 text-right tabular [&::-webkit-inner-spin-button]:appearance-none"
            />
            %
          </span>
        </div>
        <input
          id={id}
          type="range"
          min={0}
          max={100}
          step={1}
          value={percent}
          disabled={fixed}
          aria-valuetext={shown}
          aria-describedby={`${id}-hint`}
          aria-invalid={error ? true : undefined}
          onChange={(event) => setPercent(event.target.valueAsNumber)}
          className="h-6 w-full cursor-pointer accent-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-50"
        />
        <p
          id={`${id}-hint`}
          className={cn(
            "text-xs font-medium",
            error ? "text-danger-text" : "text-muted-foreground",
          )}
        >
          {error ?? samplingHint(percent, kind === "heuristic")}
        </p>
      </div>
      {fixed ? (
        <Notice>
          Sampling is fixed at 0. This rule needs an expected output. Live traces have none; only
          dataset items do, so it runs inside experiments.
        </Notice>
      ) : null}
    </SectionCard>
  );
}
