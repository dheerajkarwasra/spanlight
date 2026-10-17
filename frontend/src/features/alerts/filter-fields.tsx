import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useId, type ComponentProps } from "react";
import { useWatch, type UseFormReturn } from "react-hook-form";

import { FormField } from "@/components/form-field";
import { Input } from "@/components/ui/input";
import { projectsApi, queryKeys } from "@/lib/api";
import { cn } from "@/lib/utils";

import { ErrorFilterFields } from "./error-filter-fields";
import { takesErrorFilters } from "./rule-filters";
import { RULE_EDITOR_COPY } from "./rule-form-options";
import { FILTER_MAX_LENGTH, type FilterField, type RuleFormValues } from "./rule-form-schema";

/** Providers the SDK and the gateway record; OTLP senders may use others, which can be typed. */
const KNOWN_PROVIDERS = ["anthropic", "openai"];

const FILTER_LABELS: Record<FilterField, { label: string; placeholder: string }> = {
  environment: { label: "Environment", placeholder: "Any environment" },
  provider: { label: "Provider", placeholder: "Any provider" },
  model: { label: "Model", placeholder: "Any model" },
};

/** The project's environments and models seen in its traces. Same cache entry as the shell's. */
function useFilterSuggestions(projectId: string) {
  return useQuery({
    queryKey: queryKeys.project(projectId).filters,
    queryFn: () => projectsApi.filters(projectId),
    staleTime: 5 * 60_000,
  });
}

interface FilterFieldsProps {
  form: UseFormReturn<RuleFormValues>;
  projectId: string;
}

/**
 * Figma "Filters": environment, provider and model, each left empty for "Any". The values are
 * exact matches, so each field suggests what the project's traces already carry but accepts any
 * name, e.g. an environment that has no traffic yet. For Error rate and Failed LLM calls a second
 * row adds Error class and Rate-limit kind (Figma "Alerts/Rule editor/Error filters").
 */
export function FilterFields({ form, projectId }: FilterFieldsProps) {
  const suggestions = useFilterSuggestions(projectId).data;
  const metric = useWatch({ control: form.control, name: "metric" });
  const errorFilters = takesErrorFilters(metric);
  const errors = form.formState.errors;
  const lists: Record<FilterField, readonly string[]> = {
    environment: suggestions?.environments ?? [],
    provider: KNOWN_PROVIDERS,
    model: suggestions?.models ?? [],
  };

  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 text-label font-semibold text-foreground">Filters</legend>
      <div className="grid gap-3 sm:grid-cols-3">
        {(["environment", "provider", "model"] as const).map((field) => (
          <FormField key={field} label={FILTER_LABELS[field].label} error={errors[field]?.message}>
            <SuggestInput
              suggestions={lists[field]}
              placeholder={FILTER_LABELS[field].placeholder}
              maxLength={FILTER_MAX_LENGTH}
              {...form.register(field)}
            />
          </FormField>
        ))}
        {errorFilters ? <ErrorFilterFields form={form} /> : null}
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        {errorFilters ? RULE_EDITOR_COPY.errorFiltersHint : RULE_EDITOR_COPY.filtersHint}
      </p>
    </fieldset>
  );
}

interface SuggestInputProps extends ComponentProps<"input"> {
  suggestions: readonly string[];
}

/** A text field with the browser's own suggestion list, drawn like the Figma select field. */
function SuggestInput({ suggestions, className, ...props }: SuggestInputProps) {
  const listId = useId();
  return (
    <div className="relative">
      <Input
        list={listId}
        autoComplete="off"
        spellCheck={false}
        className={cn("pr-10 [&::-webkit-calendar-picker-indicator]:opacity-0", className)}
        {...props}
      />
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-3.5 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <datalist id={listId}>
        {suggestions.map((value) => (
          <option key={value} value={value} />
        ))}
      </datalist>
    </div>
  );
}
