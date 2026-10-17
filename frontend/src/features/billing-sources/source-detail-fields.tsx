import { Download } from "lucide-react";
import { useState } from "react";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { ApiKeyInput } from "@/features/gateway";
import type { BillingProvider, BillingSource } from "@/lib/api";

import { downloadCsvTemplate } from "./csv-upload";
import { RotateSourceDialog } from "./rotate-source-dialog";
import {
  ADMIN_KEY_MAX_LENGTH,
  PROVIDER_SCOPE_MAX_LENGTH,
  adminKeyHint,
  isApiKind,
  type ApiSourceKind,
  type FieldErrors,
  type SourceFormValues,
} from "./source-form";
import { PROVIDER_LABELS } from "./source-model";

const PROVIDER_OPTIONS = (Object.keys(PROVIDER_LABELS) as BillingProvider[]).map((value) => ({
  value,
  label: PROVIDER_LABELS[value],
}));

const SCOPE_PLACEHOLDERS: Record<ApiSourceKind, string> = {
  anthropic_admin_api: "wrkspc_…",
  openai_costs_api: "proj_…",
};

interface SourceDetailFieldsProps {
  values: SourceFormValues;
  errors: FieldErrors;
  update: (patch: Partial<SourceFormValues>) => void;
  /** The source being edited; absent when adding one. */
  source: BillingSource | undefined;
}

/**
 * The kind's own fields (Figma "create dialog anthropic" and "create dialog csv"): for an API kind
 * the admin key (write-only; on edit, Rotate key instead) and the provider scope, plus the daily
 * fetch switch on edit; for a CSV source the provider and the template.
 */
export function SourceDetailFields({ values, errors, update, source }: SourceDetailFieldsProps) {
  const [revealed, setRevealed] = useState(false);
  const kind = values.kind;

  if (!isApiKind(kind)) {
    return source ? null : (
      <>
        <FormField
          label="Provider"
          hint="Must match the provider column of the file."
          error={errors.provider}
        >
          <FieldSelect
            value={values.provider}
            onChange={(next) => {
              update({ provider: next as BillingProvider });
            }}
            options={PROVIDER_OPTIONS}
            placeholder="Choose the provider"
          />
        </FormField>
        <Button variant="link" className="justify-self-start" onClick={downloadCsvTemplate}>
          Download the CSV template
          <Download aria-hidden />
        </Button>
      </>
    );
  }

  return (
    <>
      {source ? (
        <StoredKey source={source} />
      ) : (
        <FormField label="Admin API key" hint={adminKeyHint(kind)} error={errors.adminKey}>
          <ApiKeyInput
            revealed={revealed}
            onRevealedChange={setRevealed}
            maxLength={ADMIN_KEY_MAX_LENGTH * 2}
            value={values.adminKey}
            onChange={(event) => {
              update({ adminKey: event.target.value });
            }}
          />
        </FormField>
      )}
      <FormField
        label="Provider scope (optional)"
        hint="An Anthropic workspace id limits the report to one workspace; for OpenAI, a project id (proj_…). Leave empty for the whole account."
        error={errors.providerScope}
      >
        <Input
          autoComplete="off"
          spellCheck={false}
          maxLength={PROVIDER_SCOPE_MAX_LENGTH * 2}
          placeholder={SCOPE_PLACEHOLDERS[kind]}
          value={values.providerScope}
          onChange={(event) => {
            update({ providerScope: event.target.value });
          }}
        />
      </FormField>
      {source ? (
        <EnabledSwitch
          enabled={values.enabled}
          onChange={(enabled) => {
            update({ enabled });
          }}
        />
      ) : null}
    </>
  );
}

/** On edit the key is never shown or sent back; it is replaced through Rotate key. */
function StoredKey({ source }: { source: BillingSource }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-input bg-surface-muted px-3.5 py-3">
      <div className="min-w-0">
        <p className="text-label font-semibold text-foreground">Admin API key</p>
        <p className="text-xs font-medium text-muted-foreground">
          {source.key_set ? "Stored encrypted. Never shown again." : "No key stored."}
        </p>
      </div>
      <RotateSourceDialog
        source={source}
        trigger={
          <Button size="sm" aria-label={`Rotate the key for ${source.name}`}>
            Rotate key
          </Button>
        }
      />
    </div>
  );
}

function EnabledSwitch({
  enabled,
  onChange,
}: {
  enabled: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <label className="flex items-start justify-between gap-4">
      <span className="flex flex-col gap-0.5">
        <span className="text-label font-semibold text-foreground">Fetch daily</span>
        <span className="text-xs font-medium text-muted-foreground">
          When off, the source keeps what it fetched but is not fetched again, and Refresh is
          refused.
        </span>
      </span>
      <Switch checked={enabled} onCheckedChange={onChange} />
    </label>
  );
}
