import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Skeleton } from "@/components/ui/skeleton";
import type { PromptVersion } from "@/lib/api";

import type { FormErrors, FormState } from "./experiment-form-state";
import { LoadFailed } from "./load-failed";
import { versionOptionLabel } from "./run-label";
import { usePromptListQuery, usePromptVersionsQuery } from "./use-version-index";

interface PromptTargetFieldsProps {
  state: FormState;
  errors: FormErrors;
  onChange: (patch: Partial<FormState>) => void;
}

/** The prompt and version a prompt-version target runs (Figma "Experiments — Create"). */
export function PromptTargetFields({ state, errors, onChange }: PromptTargetFieldsProps) {
  const prompts = usePromptListQuery();
  const versions = usePromptVersionsQuery(state.promptName || undefined);
  const prompt = prompts.data?.find((item) => item.name === state.promptName);

  function pickVersion(id: string) {
    const version = versions.data?.find((candidate: PromptVersion) => candidate.id === id);
    onChange({
      versionId: id,
      model: state.model || version?.model_config.model || "",
    });
  }

  if (prompts.isPending) {
    return <Skeleton className="h-[72px] w-full rounded-input" />;
  }
  if (prompts.isError) {
    return <LoadFailed what="prompts" onRetry={() => void prompts.refetch()} />;
  }
  if (prompts.data.length === 0) {
    return (
      <Notice>
        No prompts yet. Create one in Prompts, or switch to ad-hoc messages and write them here.
      </Notice>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="Prompt">
          <FieldSelect
            value={state.promptName}
            placeholder="Choose a prompt"
            onChange={(name) => onChange({ promptName: name, versionId: "" })}
            options={prompts.data.map((item) => ({ value: item.name, label: item.name }))}
          />
        </FormField>
        <FormField label="Version" error={errors.versionId}>
          <FieldSelect
            value={state.versionId}
            placeholder={
              state.promptName === ""
                ? "Choose a prompt first"
                : versions.isPending
                  ? "Loading versions…"
                  : "Choose a version"
            }
            onChange={pickVersion}
            options={(versions.data ?? []).map((version) => ({
              value: version.id,
              label: versionOptionLabel(version.version, prompt),
            }))}
          />
        </FormField>
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        The item input fills the prompt variables. A plain text input is used as{" "}
        <code className="font-mono">{"{{input}}"}</code>.
      </p>
    </div>
  );
}
