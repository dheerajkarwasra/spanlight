import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { SectionCard } from "@/components/section-card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type { Dataset } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { EvaluatorPicker } from "./evaluator-picker";
import {
  DEFAULT_MAX_COST,
  MAX_ITEMS_CEILING,
  type FormErrors,
  type FormState,
} from "./experiment-form-state";
import { TargetFields } from "./target-fields";

interface ExperimentFormProps {
  state: FormState;
  errors: FormErrors;
  onChange: (patch: Partial<FormState>) => void;
  /** Undefined while the datasets load. */
  datasets: Dataset[] | undefined;
}

/** The four cards of Figma "Experiments — Create": basics, target, evaluators and limits. */
export function ExperimentForm({ state, errors, onChange, datasets }: ExperimentFormProps) {
  const dataset = datasets?.find((item) => item.id === state.datasetId);
  return (
    <div className="flex flex-col gap-5">
      <SectionCard title="Basics" className="gap-4">
        <FormField
          label="Name"
          error={errors.name}
          hint="Each run of this experiment is listed under it, so runs can be compared."
        >
          <Input
            value={state.name}
            maxLength={200}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => onChange({ name: event.target.value })}
            className="font-mono text-xs"
          />
        </FormField>
        {datasets === undefined ? (
          <Skeleton className="h-[72px] w-full rounded-input" />
        ) : (
          <FormField
            label="Dataset"
            error={errors.datasetId}
            hint={
              datasets.length === 0
                ? "No datasets yet. Create one under Datasets, from traces or a file."
                : "Items run in id order, four at a time."
            }
          >
            <FieldSelect
              value={state.datasetId}
              placeholder="Choose a dataset"
              onChange={(datasetId) => onChange({ datasetId })}
              options={datasets.map((item) => ({
                value: item.id,
                label: `${item.name} · ${formatInteger(item.item_count) ?? 0} items`,
              }))}
            />
          </FormField>
        )}
      </SectionCard>

      <SectionCard title="Target" description="What runs on every item." className="gap-4">
        <TargetFields state={state} errors={errors} onChange={onChange} />
      </SectionCard>

      <SectionCard
        title="Evaluators"
        description="Every item is scored by each evaluator you tick."
        className="gap-4"
      >
        <EvaluatorPicker
          selected={state.evaluatorIds}
          onChange={(evaluatorIds) => onChange({ evaluatorIds })}
        />
        <p className="text-xs font-medium text-muted-foreground">
          A judge uses its own monthly budget. When it is spent, that score stays blank for the item
          and the run carries on.
        </p>
      </SectionCard>

      <SectionCard
        title="Limits"
        description="A run stops before it could pass its cost limit."
        className="gap-4"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="Maximum cost (USD)"
            error={errors.maxCost}
            hint={`Default ${DEFAULT_MAX_COST}. The run keeps the items it finished.`}
          >
            <Input
              inputMode="decimal"
              placeholder={DEFAULT_MAX_COST}
              value={state.maxCost}
              onChange={(event) => onChange({ maxCost: event.target.value })}
            />
          </FormField>
          <FormField
            label="Maximum items"
            error={errors.maxItems}
            hint={`Up to ${formatInteger(MAX_ITEMS_CEILING)}.${
              dataset ? ` This dataset has ${formatInteger(dataset.item_count)}.` : ""
            }`}
          >
            <Input
              inputMode="numeric"
              placeholder={String(
                dataset ? Math.min(dataset.item_count, MAX_ITEMS_CEILING) : MAX_ITEMS_CEILING,
              )}
              value={state.maxItems}
              onChange={(event) => onChange({ maxItems: event.target.value })}
            />
          </FormField>
        </div>
      </SectionCard>
    </div>
  );
}
