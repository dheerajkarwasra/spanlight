import { useWatch, type UseFormReturn } from "react-hook-form";

import { FormField } from "@/components/form-field";
import { SectionCard } from "@/components/section-card";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import type { EvaluatorKind } from "@/lib/api";

import { KIND_LABELS, scoreLabel } from "./evaluator-labels";
import { EXPECTED_OUTPUT_RULES, NAME_HINT, type EvaluatorFormValues } from "./evaluator-schema";

/** Where sampling starts for each kind: rules are free, a judge and a webhook cost something. */
const DEFAULT_SAMPLING: Record<EvaluatorKind, number> = {
  heuristic: 100,
  llm_judge: 5,
  webhook: 10,
};

interface BasicsFieldsProps {
  form: UseFormReturn<EvaluatorFormValues>;
  /** Editing: the kind is fixed. */
  editing: boolean;
}

/** Figma "Basics": the name (the score name on traces) and the kind. */
export function BasicsFields({ form, editing }: BasicsFieldsProps) {
  const [name, kind] = useWatch({ control: form.control, name: ["name", "kind"] });
  const errors = form.formState.errors;
  const options = (["heuristic", "llm_judge", "webhook"] as const).map((value) => ({
    value,
    label: KIND_LABELS[value],
    disabled: editing && value !== kind,
  }));

  return (
    <SectionCard title="Basics" className="gap-4">
      <FormField
        label="Name"
        error={errors.name?.message}
        hint={
          <>
            Shown on traces as the score name: {scoreLabel(name.trim() || "name")}. {NAME_HINT}
          </>
        }
      >
        <Input
          autoComplete="off"
          spellCheck={false}
          autoFocus={!editing}
          maxLength={64}
          placeholder="relevance"
          className="font-mono text-[13px]"
          {...form.register("name")}
        />
      </FormField>
      <div className="flex flex-col gap-2">
        <p className="text-sm font-semibold text-foreground">Kind</p>
        <SegmentedControl
          aria-label="Kind"
          tone="ink"
          value={kind}
          options={options}
          onValueChange={(next) => {
            form.setValue("kind", next, { shouldDirty: true });
            if (!editing) {
              // A rule that needs an expected output stays at 0 when the kind comes back to it.
              const fixed =
                next === "heuristic" && EXPECTED_OUTPUT_RULES.has(form.getValues("rule"));
              form.setValue("samplingPercent", fixed ? 0 : DEFAULT_SAMPLING[next]);
            }
          }}
          className="w-full sm:w-fit"
        />
        <p className="text-xs font-medium text-muted-foreground">
          The kind cannot change after the evaluator is created.
        </p>
      </div>
    </SectionCard>
  );
}
