import { Controller, useWatch, type UseFormReturn } from "react-hook-form";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { SectionCard } from "@/components/section-card";
import { Input } from "@/components/ui/input";

import { RULE_HINTS, RULE_LABELS } from "./evaluator-labels";
import {
  caseSensitiveDefault,
  EXPECTED_OUTPUT_RULES,
  MAX_PATTERN_LENGTH,
  type EvaluatorFormValues,
  type RuleKind,
} from "./evaluator-schema";
import { ToggleTile } from "./toggle-tile";

const RULE_ORDER: readonly RuleKind[] = [
  "contains",
  "regex",
  "json_valid",
  "length",
  "exact_match",
  "levenshtein",
];
const RULE_OPTIONS = RULE_ORDER.map((value) => ({ value, label: RULE_LABELS[value] }));
const TARGET_OPTIONS = [
  { value: "output", label: "Output" },
  { value: "input", label: "Input" },
];
const UNIT_OPTIONS = [
  { value: "chars", label: "Characters" },
  { value: "words", label: "Words" },
];

/** Figma "Rule": the rule, what it checks, and the rule's own settings. */
export function HeuristicFields({ form }: { form: UseFormReturn<EvaluatorFormValues> }) {
  const values = useWatch({ control: form.control });
  const rule = values.rule ?? "regex";
  const errors = form.formState.errors;
  const comparesExpected = EXPECTED_OUTPUT_RULES.has(rule);

  return (
    <SectionCard
      title="Rule"
      description="A rule is checked in code, with no model call and no cost."
      className="gap-4"
    >
      <Controller
        control={form.control}
        name="rule"
        render={({ field }) => (
          <FormField label="Rule" hint={RULE_HINTS[rule]}>
            <FieldSelect
              ref={field.ref}
              value={field.value}
              options={RULE_OPTIONS}
              onChange={(next) => {
                const nextRule = next as RuleKind;
                field.onChange(nextRule);
                form.setValue("caseSensitive", caseSensitiveDefault(nextRule));
                if (EXPECTED_OUTPUT_RULES.has(nextRule)) {
                  form.setValue("samplingPercent", 0);
                }
              }}
            />
          </FormField>
        )}
      />
      {comparesExpected ? null : (
        <div className="grid gap-4 sm:grid-cols-2">
          <Controller
            control={form.control}
            name="target"
            render={({ field }) => (
              <FormField label="Check the">
                <FieldSelect
                  ref={field.ref}
                  value={field.value}
                  options={TARGET_OPTIONS}
                  onChange={field.onChange}
                />
              </FormField>
            )}
          />
          {rule === "length" ? (
            <Controller
              control={form.control}
              name="unit"
              render={({ field }) => (
                <FormField label="Count">
                  <FieldSelect
                    ref={field.ref}
                    value={field.value}
                    options={UNIT_OPTIONS}
                    onChange={field.onChange}
                  />
                </FormField>
              )}
            />
          ) : null}
        </div>
      )}
      {rule === "regex" ? (
        <FormField
          label="Pattern"
          error={errors.pattern?.message}
          hint={`Up to ${MAX_PATTERN_LENGTH} characters. Checked with a one-second time limit.`}
        >
          <Input
            autoComplete="off"
            spellCheck={false}
            maxLength={MAX_PATTERN_LENGTH}
            className="font-mono text-[13px]"
            {...form.register("pattern")}
          />
        </FormField>
      ) : null}
      {rule === "contains" ? (
        <FormField label="Phrase" error={errors.needle?.message}>
          <Input autoComplete="off" maxLength={MAX_PATTERN_LENGTH} {...form.register("needle")} />
        </FormField>
      ) : null}
      {rule === "regex" ? (
        <ToggleTile
          label="Ignore case"
          description="Treat upper and lower case letters as the same."
          checked={values.ignoreCase ?? false}
          onCheckedChange={(checked) => form.setValue("ignoreCase", checked, { shouldDirty: true })}
        />
      ) : null}
      {rule === "exact_match" ? (
        <ToggleTile
          label="Normalize whitespace"
          description="Collapse runs of spaces and line breaks before comparing."
          checked={values.normalizeWhitespace ?? true}
          onCheckedChange={(checked) =>
            form.setValue("normalizeWhitespace", checked, { shouldDirty: true })
          }
        />
      ) : null}
      {rule === "contains" || rule === "exact_match" ? (
        <ToggleTile
          label="Case sensitive"
          description={
            values.caseSensitive
              ? "On: Refund and refund count as different."
              : "Off: Refund and refund count as equal."
          }
          checked={values.caseSensitive ?? false}
          onCheckedChange={(checked) =>
            form.setValue("caseSensitive", checked, { shouldDirty: true })
          }
        />
      ) : null}
    </SectionCard>
  );
}
