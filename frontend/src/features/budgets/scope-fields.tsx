import { KeyRound } from "lucide-react";
import { useId } from "react";
import { Controller, useWatch, type UseFormReturn } from "react-hook-form";

import { Callout } from "@/components/callout";
import { FormField } from "@/components/form-field";
import { Input } from "@/components/ui/input";

import {
  METADATA_KEY_PATTERN,
  METADATA_VALUE_MAX_LENGTH,
  TRACE_NAME_MAX_LENGTH,
  type BudgetFormValues,
} from "./budget-schema";
import { useScopeSuggestions } from "./budgets-queries";
import { SuggestInput } from "./suggest-input";

const SEEN = "Seen in the last 7 days";
const mono = "font-mono text-[13px]";

interface ScopeFieldsProps {
  form: UseFormReturn<BudgetFormValues>;
}

/**
 * Figma "Budgets / Scope picker (metadata, trace name)", metadata: a key picked from the
 * promoted and recently seen keys or typed, and the exact value. A key that is not promoted gets
 * the promotion tip; the server's `UNKNOWN_SCOPE` shows as a callout under both fields.
 */
export function MetadataScopeFields({ form }: ScopeFieldsProps) {
  const hintId = useId();
  const suggestions = useScopeSuggestions();
  const [key, value] = useWatch({ control: form.control, name: ["metadataKey", "metadataValue"] });
  const errors = form.formState.errors;
  const keyError = errors.metadataKey?.type === "server" ? undefined : errors.metadataKey?.message;
  const trimmedKey = key.trim();
  const showTip =
    METADATA_KEY_PATTERN.test(trimmedKey) &&
    suggestions.promotedKeys !== undefined &&
    !suggestions.promotedKeys.includes(trimmedKey);

  return (
    <div role="group" aria-label="Metadata scope" aria-describedby={hintId} className="grid gap-2">
      <div className="grid gap-[22px] sm:grid-cols-[240px_minmax(0,1fr)] sm:gap-3">
        <Controller
          control={form.control}
          name="metadataKey"
          render={({ field }) => (
            <FormField label="Key" error={keyError}>
              <SuggestInput
                chevron
                name={field.name}
                value={field.value}
                onChange={field.onChange}
                onBlur={field.onBlur}
                suggestions={suggestions.metadataKeys.map((option) => ({
                  value: option.value,
                  ...(option.promoted ? { badge: "promoted" } : {}),
                }))}
                heading="Promoted and recent keys"
                maxLength={64}
                placeholder="customer_id"
              />
            </FormField>
          )}
        />
        <FormField label="Value" error={errors.metadataValue?.message}>
          {/* maxLength counts UTF-16 units; the schema counts code points, as the server does. */}
          <Input
            autoComplete="off"
            spellCheck={false}
            maxLength={METADATA_VALUE_MAX_LENGTH * 2}
            placeholder="acme"
            className={mono}
            {...form.register("metadataValue")}
          />
        </FormField>
      </div>
      <p id={hintId} className="text-xs font-medium text-muted-foreground">
        Spend on traces where{" "}
        <span className={mono}>
          {trimmedKey || "key"} = {value || "value"}
        </span>{" "}
        counts toward the budget. The value matches exactly, up to {METADATA_VALUE_MAX_LENGTH}{" "}
        characters.
      </p>
      {errors.metadataKey?.type === "server" ? (
        <Callout tone="danger" role="alert">
          A metadata budget’s scope is <span className={mono}>key=value</span> with a valid key.
        </Callout>
      ) : null}
      {showTip ? (
        <p className="flex items-start gap-2 rounded-tile bg-accent-subtle px-3.5 py-3 text-xs font-medium text-foreground">
          <KeyRound aria-hidden className="mt-px size-3.5 shrink-0 text-accent" />
          <span>
            Promote <span className={mono}>{trimmedKey}</span> in Settings › Project so Attribution
            shows the same spend over windows longer than 24 hours.
          </span>
        </p>
      ) : null}
    </div>
  );
}

/** Figma "Budgets / Scope picker", trace name: free text with the names seen in the last 7 days. */
export function TraceNameScopeField({ form }: ScopeFieldsProps) {
  const { traceNames } = useScopeSuggestions();
  return (
    <Controller
      control={form.control}
      name="traceName"
      render={({ field, fieldState }) => (
        <FormField
          label="Trace name"
          error={fieldState.error?.message}
          hint={
            <>
              The exact trace name, up to {TRACE_NAME_MAX_LENGTH} characters. Gateway calls match it
              from the <span className={mono}>x-spanlight-trace-name</span> header, or from the
              trace they join once its first span is stored: send the header to be sure.
            </>
          }
        >
          <SuggestInput
            name={field.name}
            value={field.value}
            onChange={field.onChange}
            onBlur={field.onBlur}
            suggestions={traceNames.map((value) => ({ value }))}
            heading={SEEN}
            // Room for surrogate pairs: the schema counts code points.
            maxLength={TRACE_NAME_MAX_LENGTH * 2}
            placeholder="summarize-thread"
          />
        </FormField>
      )}
    />
  );
}
