import { ChevronDown } from "lucide-react";
import { useId, useState } from "react";
import { Controller, useWatch, type UseFormReturn } from "react-hook-form";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { SectionCard } from "@/components/section-card";
import { Input } from "@/components/ui/input";
import type { Credential, ProviderKind } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useCredentialsQuery, usePricesQuery } from "./evaluator-queries";
import {
  BUDGET_REQUIRED_COPY,
  MAX_JUDGE_TOKENS,
  type EvaluatorFormValues,
} from "./evaluator-schema";
import { JudgePromptField } from "./judge-prompt-field";

/** Spec §11: the judge injection note on the evaluator form. */
const INJECTION_NOTE =
  "The judged text is passed to the model as data, but a model can still be steered by it. Treat scores as a signal, not proof.";

/** A judge calls one of the two fixed providers (spec §8.1). */
const JUDGE_PROVIDERS: Partial<Record<ProviderKind, string>> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
};

function credentialOptions(credentials: readonly Credential[]) {
  return credentials.flatMap((credential) => {
    const provider = JUDGE_PROVIDERS[credential.provider];
    return provider ? [{ value: credential.id, label: `${provider} · ${credential.name}` }] : [];
  });
}

/** Figma "Judge": prompt, credential, model, max tokens, budget, and temperature under Advanced. */
export function JudgeFields({ form }: { form: UseFormReturn<EvaluatorFormValues> }) {
  const errors = form.formState.errors;
  const [credentialId, temperature] = useWatch({
    control: form.control,
    name: ["credentialId", "temperature"],
  });
  const credentials = useCredentialsQuery().data ?? [];
  const provider = credentials.find((credential) => credential.id === credentialId)?.provider;
  const prices = usePricesQuery().data ?? [];
  const models = [
    ...new Set(prices.filter((price) => price.provider === provider).map((p) => p.model_pattern)),
  ];
  const modelListId = useId();
  const [advanced, setAdvanced] = useState(temperature !== "");
  const advancedId = useId();

  return (
    <SectionCard
      title="Judge"
      description="A model reads the input and output and returns a score from 0 to 1, higher is better."
      className="gap-4"
    >
      <JudgePromptField form={form} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Controller
          control={form.control}
          name="credentialId"
          render={({ field, fieldState }) => (
            <FormField label="Credential" error={fieldState.error?.message}>
              <FieldSelect
                ref={field.ref}
                value={field.value}
                placeholder="Pick a credential"
                options={credentialOptions(credentials)}
                onChange={field.onChange}
              />
            </FormField>
          )}
        />
        <FormField label="Model" error={errors.model?.message}>
          <Input
            list={modelListId}
            autoComplete="off"
            spellCheck={false}
            placeholder="claude-haiku-5-5"
            className="font-mono text-[13px]"
            {...form.register("model")}
          />
        </FormField>
        <datalist id={modelListId}>
          {models.map((model) => (
            <option key={model} value={model} />
          ))}
        </datalist>
        <FormField
          label="Max tokens"
          error={errors.maxTokens?.message}
          hint="Output limit for each judge call."
        >
          <Input
            inputMode="numeric"
            autoComplete="off"
            max={MAX_JUDGE_TOKENS}
            {...form.register("maxTokens")}
          />
        </FormField>
        <FormField
          label="Monthly budget (USD)"
          error={errors.budget?.message}
          hint={BUDGET_REQUIRED_COPY}
        >
          <Input
            inputMode="decimal"
            autoComplete="off"
            placeholder="0.50"
            className="tabular"
            {...form.register("budget")}
          />
        </FormField>
      </div>
      <div className="flex flex-col gap-3">
        <button
          type="button"
          aria-expanded={advanced}
          aria-controls={advancedId}
          onClick={() => setAdvanced((open) => !open)}
          className="inline-flex w-fit items-center gap-1.5 rounded-sm text-sm font-semibold text-accent hover:underline"
        >
          Advanced
          <ChevronDown
            aria-hidden
            className={cn("size-4 transition-transform", advanced && "rotate-180")}
          />
        </button>
        {advanced ? (
          <div id={advancedId} className="sm:w-1/2 sm:pr-2">
            <FormField
              label="Temperature"
              error={errors.temperature?.message}
              hint="Leave empty to send none. The newest Claude models accept only their default."
            >
              <Input
                inputMode="decimal"
                autoComplete="off"
                placeholder="Provider default"
                {...form.register("temperature")}
              />
            </FormField>
          </div>
        ) : null}
      </div>
      <Notice>{INJECTION_NOTE}</Notice>
    </SectionCard>
  );
}
