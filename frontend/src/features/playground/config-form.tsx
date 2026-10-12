import { useId } from "react";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Input } from "@/components/ui/input";
import type { Credential, Price } from "@/lib/api";

import { credentialLabel, modelSuggestions } from "./credential-options";

import {
  MAX_TOKENS_RULE,
  TEMPERATURE_RULE,
  TOP_P_RULE,
  effectiveCredentialId,
  parseParam,
  type ColumnState,
} from "./config-state";

interface ConfigFormProps {
  column: ColumnState;
  onChange: (patch: Partial<ColumnState>) => void;
  credentials: readonly Credential[];
  prices: readonly Price[];
}

function paramError(text: string, rule: Parameters<typeof parseParam>[1], range: string) {
  return parseParam(text, rule) === null ? `Use ${range}.` : undefined;
}

/** Credential, model and sampling parameters of one column (Figma "Playground — 1 column"). */
export function ConfigForm({ column, onChange, credentials, prices }: ConfigFormProps) {
  const listId = useId();
  const credentialId = effectiveCredentialId(
    column,
    credentials.map((credential) => credential.id),
  );
  const provider =
    credentials.find((credential) => credential.id === credentialId)?.provider ?? null;
  const suggestions = modelSuggestions(prices, provider);

  return (
    <div className="flex flex-col gap-3.5">
      <FormField label="Credential">
        <FieldSelect
          value={credentialId}
          placeholder="Choose a credential"
          onChange={(value) => onChange({ credentialId: value })}
          options={credentials.map((credential) => ({
            value: credential.id,
            label: credentialLabel(credential),
          }))}
        />
      </FormField>
      <FormField label="Model" hint="Type any model the provider serves.">
        <Input
          value={column.model}
          onChange={(event) => onChange({ model: event.target.value })}
          list={suggestions.length > 0 ? listId : undefined}
          autoComplete="off"
          spellCheck={false}
          className="font-mono text-xs"
        />
      </FormField>
      {suggestions.length > 0 ? (
        <datalist id={listId}>
          {suggestions.map((model) => (
            <option key={model} value={model} />
          ))}
        </datalist>
      ) : null}
      <div className="grid grid-cols-3 gap-2.5">
        <FormField
          label="Temperature"
          error={paramError(column.temperature, TEMPERATURE_RULE, "0 to 2")}
        >
          <Input
            inputMode="decimal"
            placeholder="Default"
            value={column.temperature}
            onChange={(event) => onChange({ temperature: event.target.value })}
            className="px-3"
          />
        </FormField>
        <FormField
          label="Max tokens"
          error={paramError(column.maxTokens, MAX_TOKENS_RULE, "1 to 4096")}
        >
          <Input
            inputMode="numeric"
            placeholder="Default"
            value={column.maxTokens}
            onChange={(event) => onChange({ maxTokens: event.target.value })}
            className="px-3"
          />
        </FormField>
        <FormField label="Top p" error={paramError(column.topP, TOP_P_RULE, "0 to 1")}>
          <Input
            inputMode="decimal"
            placeholder="Default"
            value={column.topP}
            onChange={(event) => onChange({ topP: event.target.value })}
            className="px-3"
          />
        </FormField>
      </div>
    </div>
  );
}
