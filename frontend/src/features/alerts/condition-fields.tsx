import { Controller, useWatch, type UseFormReturn } from "react-hook-form";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";

import { METRIC_LABELS, METRICS } from "./metric-labels";
import { takesErrorFilters } from "./rule-filters";
import {
  COMPARATORS,
  CONDITION_OPTIONS,
  DIRECTION_OPTIONS,
  RULE_EDITOR_COPY,
} from "./rule-form-options";
import type { RuleFormValues } from "./rule-form-schema";
import { THRESHOLD_UNITS } from "./threshold-units";
import { UnitInput } from "./unit-input";

const METRIC_OPTIONS = METRICS.map((metric) => ({
  value: metric,
  label: METRIC_LABELS[metric].label,
}));

interface ConditionFieldsProps {
  form: UseFormReturn<RuleFormValues>;
}

/**
 * What the rule compares. Threshold: Metric, Condition and Threshold in one row (the threshold in
 * the metric's own unit). Anomaly: Metric and Direction, then Baseline windows and Sensitivity.
 * Rows stack on phones.
 */
export function ConditionFields({ form }: ConditionFieldsProps) {
  const [kind, metric] = useWatch({ control: form.control, name: ["kind", "metric"] });
  const errors = form.formState.errors;
  const anomaly = kind === "anomaly";
  const unit = THRESHOLD_UNITS[metric];

  return (
    <>
      <div className={anomaly ? "grid gap-3 sm:grid-cols-2" : "grid gap-3 sm:grid-cols-3"}>
        <Controller
          control={form.control}
          name="metric"
          render={({ field, fieldState }) => (
            <FormField label="Metric" error={fieldState.error?.message}>
              <FieldSelect
                ref={field.ref}
                value={field.value}
                onChange={(next) => {
                  field.onChange(next);
                  const metricNow = form.getValues("metric");
                  if (!takesErrorFilters(metricNow)) {
                    // The error filters disappear for this metric; clear them so a hidden
                    // filter is never kept or refused.
                    form.setValue("errorClass", "");
                    form.setValue("rateLimitKind", "");
                    form.clearErrors(["errorClass", "rateLimitKind"]);
                  }
                  // The typed number now reads in the new metric's unit: check it again.
                  if (form.getValues("threshold") !== "") {
                    void form.trigger("threshold");
                  }
                }}
                options={METRIC_OPTIONS}
              />
            </FormField>
          )}
        />
        <Controller
          control={form.control}
          name="comparator"
          render={({ field, fieldState }) => (
            <FormField
              label={anomaly ? "Direction" : "Condition"}
              error={fieldState.error?.message}
            >
              <FieldSelect
                ref={field.ref}
                value={field.value}
                onChange={field.onChange}
                options={COMPARATORS.map((comparator) => ({
                  value: comparator,
                  label: (anomaly ? DIRECTION_OPTIONS : CONDITION_OPTIONS)[comparator],
                }))}
              />
            </FormField>
          )}
        />
        {anomaly ? null : (
          <FormField
            label={
              <>
                Threshold<span className="sr-only"> in {unit.spoken}</span>
              </>
            }
            error={errors.threshold?.message}
          >
            <UnitInput prefix={unit.prefix} suffix={unit.suffix} {...form.register("threshold")} />
          </FormField>
        )}
      </div>
      {anomaly ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <FormField
            label="Baseline windows"
            hint={RULE_EDITOR_COPY.baselineHint}
            error={errors.baselineWindows?.message}
          >
            <UnitInput suffix="windows" inputMode="numeric" {...form.register("baselineWindows")} />
          </FormField>
          <FormField
            label="Sensitivity"
            hint={RULE_EDITOR_COPY.sensitivityHint}
            error={errors.sensitivity?.message}
          >
            <UnitInput {...form.register("sensitivity")} />
          </FormField>
        </div>
      ) : null}
    </>
  );
}
