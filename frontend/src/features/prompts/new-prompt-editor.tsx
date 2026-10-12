import { Link, useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import { toast } from "sonner";

import { FormField } from "@/components/form-field";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { useProjectParams } from "@/features/shell";
import { toPathPromptName, type PromptKind, type PromptTemplate } from "@/lib/api";

import { BackToPrompts } from "./back-link";
import { CardTitle } from "./card-title";
import { emptyDraft, MAX_DESCRIPTION, promptNameProblem, toAdvice, type Draft } from "./draft";
import { EditorSide } from "./editor-side";
import { isProblem, promptErrorMessage } from "./prompt-errors";
import { EditorFrame, TemplateProblem } from "./prompt-editor";
import { useCreatePrompt } from "./prompt-queries";
import { TemplateEditor } from "./template-editor";
import { inspectTemplate } from "@/lib/prompt-template";

const NAME_HINT =
  "Letters, numbers, / . _ - allowed. Each part between slashes starts with a letter or number.";
const KIND_OPTIONS = [
  { value: "text", label: "Text" },
  { value: "chat", label: "Chat" },
] as const;

/** Keeps what was typed when the kind flips: text becomes one user message, and back. */
function convertTemplate(template: PromptTemplate, kind: PromptKind): PromptTemplate {
  if (template.kind === kind) {
    return template;
  }
  if (template.kind === "text") {
    return { kind: "chat", messages: [{ role: "user", content: template.text }] };
  }
  return { kind: "text", text: template.messages.map((message) => message.content).join("\n\n") };
}

/** Figma "Prompts — Editor — new prompt": name, kind, description and version 1. */
export function NewPromptEditor() {
  const { orgId, projectId } = useProjectParams();
  const navigate = useNavigate();
  const create = useCreatePrompt();
  const problemId = useId();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [draft, setDraft] = useState<Draft>(() => emptyDraft("text"));
  const [nameError, setNameError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const inspection = inspectTemplate(draft.template);
  const { advice, errors } = toAdvice(draft);
  const disabledHint = inspection.problem ? "Fix the template to save the first version." : null;
  const list = { to: "/$orgId/$projectId/prompts" as const, params: { orgId, projectId } };

  async function save() {
    const problem = promptNameProblem(name.trim());
    setNameError(problem);
    if (problem !== null || disabledHint !== null || Object.keys(errors).length > 0) {
      return;
    }
    setFailure(null);
    try {
      const created = await create.mutateAsync({
        name: name.trim(),
        kind: draft.template.kind,
        description: description.trim() || null,
        template: draft.template,
        model_config: advice,
        commit_message: draft.commitMessage.trim() || null,
      });
      toast.success(`Created ${created.name}.`);
      void navigate({
        to: "/$orgId/$projectId/prompts/$name",
        params: { orgId, projectId, name: toPathPromptName(created.name) },
      });
    } catch (error) {
      const message = promptErrorMessage(error);
      if (isProblem(error, "NAME_TAKEN")) {
        setNameError(message);
      } else {
        setFailure(message);
      }
    }
  }

  return (
    <EditorFrame
      back={<BackToPrompts />}
      title="New prompt"
      description="Name it, pick a kind and write version 1."
      onSubmit={() => void save()}
      main={
        <>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
            <FormField
              label="Name"
              hint={NAME_HINT}
              error={nameError ?? undefined}
              className="flex-1"
            >
              <Input
                value={name}
                autoComplete="off"
                spellCheck={false}
                className="font-mono text-[13px]"
                onChange={(event) => setName(event.target.value)}
              />
            </FormField>
            <div className="flex flex-col gap-2">
              <span aria-hidden className="text-label font-semibold text-foreground">
                Kind
              </span>
              <SegmentedControl
                tone="surface"
                aria-label="Kind"
                value={draft.template.kind}
                options={KIND_OPTIONS}
                onValueChange={(kind) =>
                  setDraft({ ...draft, template: convertTemplate(draft.template, kind) })
                }
              />
            </div>
          </div>
          <FormField label="Description" hint="Optional.">
            <Input
              value={description}
              maxLength={MAX_DESCRIPTION}
              onChange={(event) => setDescription(event.target.value)}
            />
          </FormField>
          <CardTitle
            as="h3"
            title="Template"
            subtitle={draft.template.kind === "chat" ? "Chat messages" : "Plain text"}
          />
          <TemplateEditor
            template={draft.template}
            onChange={(template) => setDraft({ ...draft, template })}
            base={null}
            problem={inspection.problem}
            problemId={problemId}
          />
          <TemplateProblem problem={inspection.problem} id={problemId} />
        </>
      }
      side={
        <EditorSide
          draft={draft}
          onDraftChange={setDraft}
          variables={inspection.variables}
          adviceErrors={errors}
          disabledHint={disabledHint}
          pending={create.isPending}
          failure={failure}
          cancel={<Link {...list}>Cancel</Link>}
        />
      }
    />
  );
}
