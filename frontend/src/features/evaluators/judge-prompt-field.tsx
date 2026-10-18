import { useEffect } from "react";
import { Controller, useWatch, type UseFormReturn } from "react-hook-form";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Skeleton } from "@/components/ui/skeleton";

import { useJudgePromptsQuery, usePromptVersionsQuery } from "./evaluator-queries";
import { JUDGE_TEMPLATES, type EvaluatorFormValues } from "./evaluator-schema";

/** The Template select's last option: switch to a version of one of the project's prompts. */
const OWN_PROMPT = "own-prompt";

const TEMPLATE_OPTIONS = [
  ...JUDGE_TEMPLATES,
  { value: OWN_PROMPT, label: "Use one of my prompts" },
];

const TEMPLATE_HINT =
  "Four built-in templates. The first use saves one as the prompt judges/<name>, which you can edit under Prompts; an edit makes a new version.";

type Form = UseFormReturn<EvaluatorFormValues>;

/**
 * Figma "Template": one of the four built-in judge templates (the server stores it as the
 * project's prompt `judges/<name>`), or any chat prompt version that uses only input, output and
 * expected_output.
 */
export function JudgePromptField({ form }: { form: Form }) {
  const source = useWatch({ control: form.control, name: "judgeSource" });
  return (
    <div className="flex flex-col gap-4">
      <Controller
        control={form.control}
        name="judgeTemplate"
        render={({ field, fieldState }) => (
          <FormField label="Template" hint={TEMPLATE_HINT} error={fieldState.error?.message}>
            <FieldSelect
              ref={field.ref}
              value={source === "prompt" ? OWN_PROMPT : field.value}
              options={TEMPLATE_OPTIONS}
              onChange={(next) => {
                if (next === OWN_PROMPT) {
                  form.setValue("judgeSource", "prompt", { shouldDirty: true });
                  return;
                }
                form.setValue("judgeSource", "template", { shouldDirty: true });
                field.onChange(next);
              }}
            />
          </FormField>
        )}
      />
      {source === "prompt" ? <OwnPromptFields form={form} /> : null}
    </div>
  );
}

/** A chat prompt (`judges/*` first) and one of its versions; picking a prompt takes its newest. */
function OwnPromptFields({ form }: { form: Form }) {
  const [promptName, versionId] = useWatch({
    control: form.control,
    name: ["promptName", "promptVersionId"],
  });
  const prompts = useJudgePromptsQuery();
  const versions = usePromptVersionsQuery(promptName);
  const newest = versions.data?.[0];

  useEffect(() => {
    if (versionId === "" && newest) {
      form.setValue("promptVersionId", newest.id, { shouldDirty: true });
    }
  }, [form, newest, versionId]);

  if (prompts.isPending) {
    return (
      <Skeleton role="status" aria-label="Loading prompts" className="h-[74px] rounded-input" />
    );
  }
  if (prompts.data === undefined) {
    return (
      <Notice tone="danger" role="alert">
        The project's prompts couldn't be loaded. Reload the page to try again, or use a built-in
        template.
      </Notice>
    );
  }
  if (prompts.data.length === 0) {
    return (
      <Notice>
        No chat prompts in this project yet. Create one under Prompts using the variables input,
        output and expected_output, or use a built-in template.
      </Notice>
    );
  }

  const promptOptions = prompts.data.map((prompt) => ({ value: prompt.name, label: prompt.name }));
  const versionOptions = (versions.data ?? []).map((version, index) => ({
    value: version.id,
    label: index === 0 ? `Version ${version.version} · latest` : `Version ${version.version}`,
  }));

  return (
    <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_180px]">
      <FormField label="Prompt">
        <FieldSelect
          value={promptName}
          placeholder="Pick a chat prompt"
          options={promptOptions}
          onChange={(next) => {
            form.setValue("promptName", next, { shouldDirty: true });
            form.setValue("promptVersionId", "", { shouldDirty: true });
          }}
        />
      </FormField>
      <Controller
        control={form.control}
        name="promptVersionId"
        render={({ field, fieldState }) => (
          <FormField label="Version" error={fieldState.error?.message}>
            <FieldSelect
              ref={field.ref}
              value={field.value}
              placeholder={promptName === "" ? "Pick a prompt first" : "Loading…"}
              options={versionOptions}
              onChange={field.onChange}
            />
          </FormField>
        )}
      />
    </div>
  );
}
