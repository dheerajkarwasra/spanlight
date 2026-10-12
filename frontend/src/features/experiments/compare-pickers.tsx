import { ArrowLeftRight } from "lucide-react";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { ExperimentRun } from "@/lib/api";
import { formatCost, formatInteger } from "@/lib/format";

import { isFinished, runOptionLabel, type VersionIndex } from "./run-label";

interface ComparePickersProps {
  runs: readonly ExperimentRun[];
  index: VersionIndex;
  /** The ids in the URL, and the two runs read with their totals for the hints. */
  pair: {
    a: string | undefined;
    b: string | undefined;
    runA: ExperimentRun | undefined;
    runB: ExperimentRun | undefined;
  };
  onChange: (a: string | undefined, b: string | undefined) => void;
}

function hint(run: ExperimentRun | undefined): string | undefined {
  const totals = run?.totals;
  if (!totals) {
    return undefined;
  }
  const cost = formatCost(totals.cost_usd);
  return `${formatInteger(totals.items)} items${cost ? ` · ${cost}` : " · cost unknown"}`;
}

/** Figma "Experiments — Compare" run pickers: baseline, swap, candidate. Only ended runs. */
export function ComparePickers({ runs, index, pair, onChange }: ComparePickersProps) {
  const { a, b, runA: detailA, runB: detailB } = pair;
  const options = runs
    .filter((run) => isFinished(run))
    .map((run) => ({ value: run.id, label: runOptionLabel(run, index) }));
  return (
    <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-end">
      <FormField label="Baseline (A)" hint={hint(detailA)} className="min-w-0 flex-1">
        <FieldSelect
          value={a ?? ""}
          placeholder="Pick a run"
          onChange={(value) => onChange(value, b)}
          options={options}
        />
      </FormField>
      <div className="flex justify-center sm:pb-7">
        <Button
          size="icon"
          variant="ghost"
          className="size-11 bg-surface-muted"
          aria-label="Swap baseline and candidate"
          disabled={a === undefined && b === undefined}
          onClick={() => onChange(b, a)}
        >
          <ArrowLeftRight aria-hidden className="max-sm:rotate-90" />
        </Button>
      </div>
      <FormField label="Candidate (B)" hint={hint(detailB)} className="min-w-0 flex-1">
        <FieldSelect
          value={b ?? ""}
          placeholder="Pick a run"
          onChange={(value) => onChange(a, value)}
          options={options}
        />
      </FormField>
    </Card>
  );
}
