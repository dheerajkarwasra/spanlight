import { Info } from "lucide-react";
import { Controller, useWatch, type UseFormReturn } from "react-hook-form";

import { FormField } from "@/components/form-field";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { GatewayKey } from "@/lib/api";

import { SCOPE_LABELS } from "./budget-period";
import { SCOPE_ID_MAX_LENGTH, SCOPES, type BudgetFormValues } from "./budget-schema";
import { useGatewayKeysQuery } from "./budgets-queries";
import { MetadataScopeFields, TraceNameScopeField } from "./scope-fields";

interface ScopePickerProps {
  form: UseFormReturn<BudgetFormValues>;
  projectName: string;
}

/**
 * Figma budget dialog "Scope" and "Budgets / Scope picker (metadata, trace name)": Project,
 * Gateway key, End user, Model, Metadata or Trace name (§2.3: no SDK API keys; 3 × 2 on phones),
 * then the scope's own value: nothing for the project, a key of this project, free text for an
 * end user or a model, a metadata key and value, or a trace name.
 */
export function ScopePicker({ form, projectName }: ScopePickerProps) {
  const scope = useWatch({ control: form.control, name: "scope" });
  const errors = form.formState.errors;

  return (
    <>
      <div className="flex flex-col gap-2">
        <p className="text-sm font-semibold text-foreground">Scope</p>
        <SegmentedControl
          aria-label="Scope"
          tone="surface"
          value={scope}
          options={SCOPES.map((value) => ({ value, label: SCOPE_LABELS[value] }))}
          onValueChange={(next) => {
            form.setValue("scope", next);
            form.clearErrors([
              "gatewayKeyId",
              "userId",
              "model",
              "metadataKey",
              "metadataValue",
              "traceName",
            ]);
          }}
          className="grid w-full grid-cols-3 rounded-tile md:grid-cols-6 md:rounded-full max-md:[&>*]:min-h-11 md:[&>*]:px-2"
        />
      </div>
      {scope === "project" ? (
        <p className="flex items-center gap-2 rounded-tile bg-surface-muted px-4 py-3 text-xs font-medium text-muted-foreground">
          <Info aria-hidden className="size-3.5 shrink-0" />
          Counts all spend in {projectName}.
        </p>
      ) : null}
      {scope === "gateway_key" ? (
        <Controller
          control={form.control}
          name="gatewayKeyId"
          render={({ field, fieldState }) => (
            <FormField
              label="Gateway key"
              hint="Spend through this key counts toward the budget."
              error={fieldState.error?.message}
            >
              <GatewayKeySelect value={field.value} onChange={field.onChange} />
            </FormField>
          )}
        />
      ) : null}
      {scope === "user" ? (
        <FormField
          label="End user ID"
          hint={`The user ID your app sends on traces and gateway calls. Up to ${String(SCOPE_ID_MAX_LENGTH)} characters.`}
          error={errors.userId?.message}
        >
          <Input
            autoComplete="off"
            spellCheck={false}
            maxLength={SCOPE_ID_MAX_LENGTH}
            placeholder="user_7f3a91"
            className="font-mono text-[13px]"
            {...form.register("userId")}
          />
        </FormField>
      ) : null}
      {scope === "model" ? (
        <FormField
          label="Model"
          hint="The exact model name, for example claude-sonnet-4-5 or gpt-4.1-mini."
          error={errors.model?.message}
        >
          <Input
            autoComplete="off"
            spellCheck={false}
            maxLength={SCOPE_ID_MAX_LENGTH}
            placeholder="gpt-4.1"
            className="font-mono text-[13px]"
            {...form.register("model")}
          />
        </FormField>
      ) : null}
      {scope === "metadata" ? <MetadataScopeFields form={form} /> : null}
      {scope === "trace_name" ? <TraceNameScopeField form={form} /> : null}
    </>
  );
}

interface GatewayKeySelectProps {
  value: string;
  onChange: (value: string) => void;
  /** Wired by `FormField` onto the trigger. */
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

/** Active keys of the project, plus the budget's own key if it has been revoked since. */
function GatewayKeySelect({ value, onChange, ...aria }: GatewayKeySelectProps) {
  const keys = useGatewayKeysQuery();
  const usable = (keys.data ?? []).filter(
    (key: GatewayKey) => key.revoked_at === null || key.id === value,
  );
  const placeholder = keys.isPending
    ? "Loading keys…"
    : keys.isError
      ? "Couldn't load the keys"
      : usable.length === 0
        ? "This project has no gateway keys"
        : "Pick a key";

  return (
    <Select value={value} onValueChange={onChange} disabled={usable.length === 0}>
      <SelectTrigger {...aria}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {usable.map((key) => (
          <SelectItem key={key.id} value={key.id}>
            <span className="font-mono text-[13px]">{key.name}</span>
            {key.revoked_at === null ? null : (
              <span className="text-muted-foreground"> · revoked</span>
            )}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
