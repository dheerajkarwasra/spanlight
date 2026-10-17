import { Controller, type UseFormReturn } from "react-hook-form";

import { FieldSelect, type FieldSelectOption } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { SeriesKey } from "@/components/chart-parts";
import { ERROR_CLASSES, errorClassColor, errorClassLabel, isErrorClass } from "@/lib/error-class";
import { RATE_LIMIT_KIND_ORDER, rateLimitKindLabel } from "@/lib/rate-limit-kind";

import type { RuleFormValues } from "./rule-form-schema";

/** The "Any" item's value; the form keeps "" for any, which shows the placeholder. */
const ANY = "__any__";

const CLASS_OPTIONS: readonly FieldSelectOption[] = [
  { value: ANY, label: "Any class" },
  ...ERROR_CLASSES.map((errorClass) => ({
    value: errorClass,
    label: errorClassLabel(errorClass),
    leading: (
      <span className="mr-2.5 inline-flex">
        <SeriesKey color={errorClassColor(errorClass)} shape="bar" />
      </span>
    ),
  })),
];

const KIND_OPTIONS: readonly FieldSelectOption[] = [
  { value: ANY, label: "Any kind" },
  ...RATE_LIMIT_KIND_ORDER.map((kind) => ({
    value: kind,
    label: rateLimitKindLabel(kind),
    trailing: <span className="ml-2 font-mono text-label text-muted-foreground">{kind}</span>,
  })),
];

interface ErrorFilterFieldsProps {
  form: UseFormReturn<RuleFormValues>;
}

/**
 * Figma "Alerts/Rule editor/Error filters": Error class (in the shared class order, with its
 * colour key) and Rate-limit kind (with its stored value in mono), each "Any" by default. Shown
 * for Error rate and Failed LLM calls only; the parent removes them for other metrics.
 */
export function ErrorFilterFields({ form }: ErrorFilterFieldsProps) {
  return (
    <>
      <Controller
        control={form.control}
        name="errorClass"
        render={({ field, fieldState }) => (
          <FormField label="Error class" error={fieldState.error?.message}>
            <FieldSelect
              ref={field.ref}
              value={field.value}
              placeholder="Any class"
              options={CLASS_OPTIONS}
              onChange={(next) => {
                field.onChange(isErrorClass(next) ? next : "");
              }}
            />
          </FormField>
        )}
      />
      <Controller
        control={form.control}
        name="rateLimitKind"
        render={({ field, fieldState }) => (
          <FormField label="Rate-limit kind" error={fieldState.error?.message}>
            <FieldSelect
              ref={field.ref}
              value={field.value}
              placeholder="Any kind"
              options={KIND_OPTIONS}
              onChange={(next) => {
                field.onChange(RATE_LIMIT_KIND_ORDER.find((kind) => kind === next) ?? "");
              }}
            />
          </FormField>
        )}
      />
    </>
  );
}
