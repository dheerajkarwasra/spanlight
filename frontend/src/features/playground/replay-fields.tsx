import { useId } from "react";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Input } from "@/components/ui/input";
import type { Credential, ReplayProviderKind } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { MAX_TOKENS_RULE, parseParam } from "./config-state";
import { credentialBaseUrl, credentialLabel, modelSuggestions } from "./credential-options";
import { usePricesQuery } from "./playground-queries";
import { MAX_REPLAY_TOKENS } from "./replay-state";

/** What the user picks before "Run replay"; the request itself is the column's. */
export interface ReplayDraft {
  credentialId: string | null;
  model: string;
  /** Empty sends the stored value (capped by the server). */
  maxTokens: string;
}

const KIND_NAMES: Record<ReplayProviderKind, string> = {
  openai: "OpenAI or OpenAI-compatible",
  anthropic: "Anthropic",
};

interface ReplayFieldsProps {
  draft: ReplayDraft;
  onChange: (patch: Partial<ReplayDraft>) => void;
  /** The organization's credentials of the span's provider family only. */
  credentials: readonly Credential[];
  kind: ReplayProviderKind;
  storedMaxTokens: number | null;
}

function maxTokensHint(stored: number | null): string {
  const cap = formatInteger(MAX_REPLAY_TOKENS) ?? String(MAX_REPLAY_TOKENS);
  return stored === null
    ? `The stored request sets none; the provider's default applies, capped at ${cap}.`
    : `Defaults to the stored value (${formatInteger(stored) ?? String(stored)}), capped at ${cap}.`;
}

/** Figma "Playground / Replay column": credential (with its base URL), model and max tokens. */
export function ReplayFields({
  draft,
  onChange,
  credentials,
  kind,
  storedMaxTokens,
}: ReplayFieldsProps) {
  const listId = useId();
  const prices = usePricesQuery();
  const selected = credentials.find((credential) => credential.id === draft.credentialId) ?? null;
  const suggestions = modelSuggestions(prices.data ?? [], selected?.provider ?? null);
  const tokensInvalid = parseParam(draft.maxTokens, MAX_TOKENS_RULE) === null;

  return (
    <div className="flex flex-col gap-3.5">
      {credentials.length === 0 ? (
        <Notice>
          This organization has no {KIND_NAMES[kind]} credential, which this span's request needs.
          An owner adds one under Gateway, Credentials.
        </Notice>
      ) : (
        <FormField
          label="Credential"
          hint={
            selected
              ? `Sends to ${credentialBaseUrl(selected)}. Only credentials that speak this span's API are listed.`
              : "Only credentials that speak this span's API are listed."
          }
        >
          <FieldSelect
            value={draft.credentialId ?? ""}
            placeholder="Choose a credential"
            onChange={(credentialId) => onChange({ credentialId })}
            options={credentials.map((credential) => ({
              value: credential.id,
              label: credentialLabel(credential),
              trailing: (
                <span className="font-mono text-2xs text-muted-foreground">
                  {credentialBaseUrl(credential)}
                </span>
              ),
            }))}
          />
        </FormField>
      )}
      <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
        <FormField label="Model" hint="Same provider only.">
          <Input
            value={draft.model}
            onChange={(event) => onChange({ model: event.target.value })}
            list={suggestions.length > 0 ? listId : undefined}
            autoComplete="off"
            spellCheck={false}
            className="font-mono text-xs"
          />
        </FormField>
        <FormField
          label="Max tokens"
          hint={maxTokensHint(storedMaxTokens)}
          error={tokensInvalid ? "Use 1 to 4096." : undefined}
        >
          <Input
            inputMode="numeric"
            placeholder="Stored value"
            value={draft.maxTokens}
            onChange={(event) => onChange({ maxTokens: event.target.value })}
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
    </div>
  );
}
