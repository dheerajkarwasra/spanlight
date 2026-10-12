import { useState } from "react";

import { ErrorState } from "@/components/error-state";
import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/input";
import { errorMessage } from "@/lib/api";
import { formatPromptVersion } from "@/lib/format";

import { pickVersion, type ColumnState } from "./config-state";
import { optionValue, parseOptionValue, promptOptions, type PromptOption } from "./prompt-options";
import { usePromptVersionFetcher, usePromptsQuery } from "./playground-queries";

interface PromptSourceFieldsProps {
  column: ColumnState;
  onChange: (patch: Partial<ColumnState>) => void;
}

function Variables({ column, onChange }: PromptSourceFieldsProps) {
  const names = column.version?.variables ?? [];
  if (names.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="text-overline text-subtle-foreground uppercase">Variables</p>
      {names.map((name) => (
        <FormField key={name} label={<span className="font-mono text-xs font-normal">{name}</span>}>
          <Textarea
            rows={1}
            value={column.variables[name] ?? ""}
            onChange={(event) =>
              onChange({ variables: { ...column.variables, [name]: event.target.value } })
            }
            className="field-sizing-content max-h-48 min-h-[46px] py-3"
          />
        </FormField>
      ))}
    </div>
  );
}

/** The options plus the column's version when it has no label (opened from a prompt's page). */
function withPicked(options: PromptOption[], column: ColumnState): PromptOption[] {
  const picked = column.version;
  if (!picked) {
    return options;
  }
  const value = optionValue(picked.promptName, picked.version);
  if (options.some((option) => option.value === value)) {
    return options;
  }
  const label = formatPromptVersion(picked.promptName, picked.version);
  return [{ value, label, name: picked.promptName, version: picked.version }, ...options];
}

/** The prompt-version source of a column: a picker, then one field per template variable. */
export function PromptSourceFields({ column, onChange }: PromptSourceFieldsProps) {
  const prompts = usePromptsQuery();
  const fetchVersion = usePromptVersionFetcher();
  const [problem, setProblem] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function pick(value: string) {
    const target = parseOptionValue(value);
    if (!target) {
      return;
    }
    setProblem(null);
    setLoading(true);
    try {
      const version = await fetchVersion(target.name, target.version);
      onChange(pickVersion(column, target.name, version));
    } catch (error) {
      setProblem(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }

  if (prompts.isPending) {
    return <Skeleton className="h-[46px] w-full rounded-input" />;
  }
  if (prompts.isError) {
    return (
      <ErrorState
        compact
        error={prompts.error}
        title="Couldn't load prompts"
        onRetry={() => void prompts.refetch()}
      />
    );
  }
  const options = withPicked(promptOptions(prompts.data), column);
  if (options.length === 0) {
    return (
      <Notice>
        No prompts yet. Create one in Prompts, or switch to Messages and write the prompt here.
      </Notice>
    );
  }
  return (
    <div className="flex flex-col gap-3.5">
      <FormField label="Prompt" error={problem ?? undefined}>
        <FieldSelect
          value={
            column.version ? optionValue(column.version.promptName, column.version.version) : ""
          }
          placeholder={loading ? "Loading version…" : "Pick a prompt version"}
          onChange={(value) => void pick(value)}
          options={options.map(({ value, label }) => ({ value, label }))}
        />
      </FormField>
      <Variables column={column} onChange={onChange} />
    </div>
  );
}
