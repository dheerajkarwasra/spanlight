import { useId } from "react";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import type { Credential, ProviderKind } from "@/lib/api";

import type { FormErrors, FormState, TargetSource } from "./experiment-form-state";
import { useCredentialsQuery, usePricesQuery } from "./experiment-queries";
import { MessagesFields } from "./messages-fields";
import { PromptTargetFields } from "./prompt-target-fields";

const PROVIDERS: Record<ProviderKind, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  openai_compatible: "OpenAI-compatible",
};

const SOURCES = [
  { value: "prompt_version", label: "Prompt version" },
  { value: "messages", label: "Ad-hoc messages" },
] as const;

interface TargetFieldsProps {
  state: FormState;
  errors: FormErrors;
  onChange: (patch: Partial<FormState>) => void;
}

function credentialLabel(credential: Credential): string {
  return `${PROVIDERS[credential.provider]} · ${credential.name}`;
}

/** The Target card's fields: what runs on every item, with which credential and model. */
export function TargetFields({ state, errors, onChange }: TargetFieldsProps) {
  const listId = useId();
  const credentials = useCredentialsQuery();
  const prices = usePricesQuery();
  const provider = credentials.data?.find((item) => item.id === state.credentialId)?.provider;
  const suggestions =
    provider === undefined || provider === "openai_compatible"
      ? []
      : [
          ...new Set(
            (prices.data ?? [])
              .filter((price) => price.provider === provider)
              .map((p) => p.model_pattern),
          ),
        ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <p aria-hidden className="text-label font-semibold text-foreground">
          Source
        </p>
        <SegmentedControl<TargetSource>
          aria-label="Source"
          tone="surface"
          value={state.source}
          onValueChange={(source) => onChange({ source })}
          options={SOURCES}
          className="self-start"
        />
      </div>
      {state.source === "prompt_version" ? (
        <PromptTargetFields state={state} errors={errors} onChange={onChange} />
      ) : (
        <MessagesFields
          messages={state.messages}
          error={errors.messages}
          onChange={(messages) => onChange({ messages })}
        />
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField
          label="Credential"
          error={errors.credentialId}
          hint={
            credentials.data?.length === 0
              ? "No credentials yet. Add one under Gateway, Credentials."
              : undefined
          }
        >
          <FieldSelect
            value={state.credentialId}
            placeholder={credentials.isPending ? "Loading credentials…" : "Choose a credential"}
            onChange={(credentialId) => onChange({ credentialId })}
            options={(credentials.data ?? []).map((credential) => ({
              value: credential.id,
              label: credentialLabel(credential),
            }))}
          />
        </FormField>
        <FormField label="Model" error={errors.model}>
          <Input
            value={state.model}
            onChange={(event) => onChange({ model: event.target.value })}
            list={suggestions.length > 0 ? listId : undefined}
            autoComplete="off"
            spellCheck={false}
            className="font-mono text-xs"
          />
        </FormField>
      </div>
      {suggestions.length > 0 ? (
        <datalist id={listId}>
          {suggestions.map((model) => (
            <option key={model} value={model} />
          ))}
        </datalist>
      ) : null}
      <div className="flex flex-col gap-2">
        <div className="grid grid-cols-3 gap-3 sm:gap-4">
          <FormField label="Temperature" error={errors.temperature}>
            <Input
              inputMode="decimal"
              placeholder="Default"
              value={state.temperature}
              onChange={(event) => onChange({ temperature: event.target.value })}
            />
          </FormField>
          <FormField label="Max tokens" error={errors.maxTokens}>
            <Input
              inputMode="numeric"
              placeholder="Default"
              value={state.maxTokens}
              onChange={(event) => onChange({ maxTokens: event.target.value })}
            />
          </FormField>
          <FormField label="Top p" error={errors.topP}>
            <Input
              inputMode="decimal"
              placeholder="Default"
              value={state.topP}
              onChange={(event) => onChange({ topP: event.target.value })}
            />
          </FormField>
        </div>
        <p className="text-xs font-medium text-muted-foreground">
          Temperature 0 to 2, max tokens up to 4,096, top p 0 to 1. Leave one blank to use the
          provider's default.
        </p>
      </div>
    </div>
  );
}
