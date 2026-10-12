import { Controller, type UseFormReturn } from "react-hook-form";

import { ChipMultiSelect } from "@/components/chip-multi-select";
import { FormField } from "@/components/form-field";
import { SectionCard } from "@/components/section-card";
import { Input } from "@/components/ui/input";

import { useFilterSuggestionsQuery } from "./evaluator-queries";
import { MAX_FILTER_VALUES, type EvaluatorFormValues } from "./evaluator-schema";

type ListField = "environments" | "models" | "tags";

const LIST_FIELDS: { name: ListField; label: string; placeholder: string }[] = [
  { name: "environments", label: "Environments", placeholder: "All environments" },
  { name: "models", label: "Models", placeholder: "All models" },
  { name: "tags", label: "Tags", placeholder: "Any tag" },
];

/**
 * Figma "Filters": environments, models and tags the project's traces carry, trace names and a
 * minimum duration. Every filter narrows; an empty one matches everything.
 */
export function FilterFields({ form }: { form: UseFormReturn<EvaluatorFormValues> }) {
  const suggestions = useFilterSuggestionsQuery().data;
  const errors = form.formState.errors;

  return (
    <SectionCard
      title="Filters"
      description="Only traces that match every filter are sampled. Leave a filter empty to match all."
      className="gap-4"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        {LIST_FIELDS.map(({ name, label, placeholder }) => (
          <Controller
            key={name}
            control={form.control}
            name={name}
            render={({ field, fieldState }) => (
              <FormField label={label} error={fieldState.error?.message}>
                <ChipMultiSelect
                  value={field.value}
                  onChange={field.onChange}
                  options={(suggestions?.[name] ?? []).map((value) => ({ value, label: value }))}
                  placeholder={placeholder}
                  label={label}
                  max={MAX_FILTER_VALUES}
                />
              </FormField>
            )}
          />
        ))}
        <FormField
          label="Trace names"
          error={errors.traceNames?.message}
          hint="Separate names with commas."
        >
          <Input autoComplete="off" placeholder="support/answer" {...form.register("traceNames")} />
        </FormField>
        <FormField label="Minimum duration (ms)" error={errors.minDurationMs?.message}>
          <Input
            inputMode="numeric"
            autoComplete="off"
            placeholder="Any duration"
            {...form.register("minDurationMs")}
          />
        </FormField>
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        Traces in the evals, playground, experiments and doctor environments are never sampled, so a
        judge cannot score itself.
      </p>
    </SectionCard>
  );
}
