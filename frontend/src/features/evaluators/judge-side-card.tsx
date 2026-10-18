import { useWatch, type UseFormReturn } from "react-hook-form";

import { SectionCard } from "@/components/section-card";
import { Badge } from "@/components/ui/badge";
import type { PromptVersion } from "@/lib/api";

import {
  JUDGE_PROMPT_PREFIX,
  useJudgePromptsQuery,
  usePromptVersionsQuery,
} from "./evaluator-queries";
import { JUDGE_TEMPLATES, type EvaluatorFormValues } from "./evaluator-schema";

/** `{{name}}` in violet, the rest as typed. */
function TemplateText({ text }: { text: string }) {
  return (
    <pre className="rounded-input bg-surface-muted p-3.5 font-mono text-xs leading-relaxed [overflow-wrap:anywhere] whitespace-pre-wrap text-foreground">
      {text.split(/(\{\{\s*[\w.]+\s*\}\})/).map((part, index) =>
        /^\{\{/.test(part) ? (
          <span key={index} className="text-accent">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </pre>
  );
}

function JudgePromptPreview({ version }: { version: PromptVersion | undefined }) {
  if (version === undefined || version.template.kind !== "chat") {
    return (
      <p className="text-sm text-muted-foreground">
        Pick a prompt and version to see what the judge gets.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {version.template.messages.map((message, index) => (
        <div key={index} className="flex flex-col gap-1.5">
          <p className="text-overline text-subtle-foreground uppercase">{message.role}</p>
          <TemplateText text={message.content} />
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-muted-foreground">
        Variables
        {version.variables.map((variable) => (
          <Badge key={variable} variant="accent" size="sm" className="font-mono">
            {variable}
          </Badge>
        ))}
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        Input and output are cut to 8,000 characters each before they reach the judge.
      </p>
    </div>
  );
}

/**
 * Figma "Judge prompt": the messages the judge gets. A built-in template already saved in this
 * project previews its latest version (what the evaluator will use); one not saved yet is
 * described, since the server stores it on create.
 */
export function JudgeSideCard({ form }: { form: UseFormReturn<EvaluatorFormValues> }) {
  const [source, template, promptName, versionId] = useWatch({
    control: form.control,
    name: ["judgeSource", "judgeTemplate", "promptName", "promptVersionId"],
  });
  const templateName = `${JUDGE_PROMPT_PREFIX}${template}`;
  const prompts = useJudgePromptsQuery().data;
  const saved = prompts?.some((prompt) => prompt.name === templateName) ?? false;
  const shownName = source === "template" ? (saved ? templateName : "") : promptName;
  const versions = usePromptVersionsQuery(shownName).data;
  const version =
    source === "template"
      ? versions?.[0]
      : versions?.find((candidate) => candidate.id === versionId);
  const label = JUDGE_TEMPLATES.find((option) => option.value === template)?.label ?? template;

  return (
    <SectionCard
      title="Judge prompt"
      description="Preview of the template. Variables are filled from the trace."
      className="gap-4"
    >
      {source === "template" && !saved ? (
        <p className="text-sm text-muted-foreground">
          The built-in {label} template. Creating the evaluator saves it in this project as the
          prompt {templateName} v1, where you can read and edit it under Prompts.
        </p>
      ) : (
        <>
          {source === "template" ? (
            <p className="text-xs font-medium text-muted-foreground">
              Uses the latest version of {templateName} in this project.
            </p>
          ) : null}
          <JudgePromptPreview version={version} />
        </>
      )}
    </SectionCard>
  );
}
