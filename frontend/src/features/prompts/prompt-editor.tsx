import { Link, useNavigate } from "@tanstack/react-router";
import { CircleAlert } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { Card } from "@/components/ui/card";
import type { Prompt, PromptVersion } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { BackToPrompt } from "./back-link";
import { CardTitle } from "./card-title";
import { draftFromVersion, hasChanges, toAdvice } from "./draft";
import { EditorSide } from "./editor-side";
import { KindChip } from "./label-badges";
import { promptErrorMessage, SAVE_DISABLED_HINT } from "./prompt-errors";
import { useCreateVersion } from "./prompt-queries";
import { TemplateEditor } from "./template-editor";
import { usePromptLink } from "./use-prompt-links";
import {
  describeTemplateError,
  inspectTemplate,
  type InvalidTemplateError,
} from "@/lib/prompt-template";

interface EditorFrameProps {
  back: ReactNode;
  title: ReactNode;
  description: string;
  /** The left card's content: fields and the template editor. */
  main: ReactNode;
  side: ReactNode;
  onSubmit: () => void;
}

/** Figma "Prompts — Editor": the template on the left, advice and the save bar on the right. */
export function EditorFrame({ back, title, description, main, side, onSubmit }: EditorFrameProps) {
  return (
    <form
      noValidate
      className="flex flex-col gap-4 md:gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      {back}
      <PageHeader title={title} description={description} />
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_420px] lg:gap-6">
        <Card className="flex min-w-0 flex-col gap-4 p-4 sm:p-6">{main}</Card>
        {side}
      </div>
    </form>
  );
}

/** The sentence under the template that says why it can't be read. */
export function TemplateProblem({
  problem,
  id,
}: {
  problem: InvalidTemplateError | null;
  id: string;
}) {
  if (problem === null) {
    return null;
  }
  return (
    <p id={id} className="flex items-start gap-1.5 text-xs font-medium text-danger-text">
      <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
      {describeTemplateError(problem)}
    </p>
  );
}

interface NewVersionEditorProps {
  prompt: Prompt;
  /** The version the draft starts from. */
  base: PromptVersion;
}

/** A new version of an existing prompt, starting as a copy of `base`. */
export function NewVersionEditor({ prompt, base }: NewVersionEditorProps) {
  const [draft, setDraft] = useState(() => draftFromVersion(base));
  const [failure, setFailure] = useState<string | null>(null);
  const create = useCreateVersion(prompt.name);
  const navigate = useNavigate();
  const detail = usePromptLink(prompt.name, { v: base.version });
  const problemId = useId();

  const inspection = inspectTemplate(draft.template);
  const { advice, errors } = toAdvice(draft);
  const disabledHint = inspection.problem
    ? "Fix the template to save a new version."
    : hasChanges(draft, base)
      ? null
      : SAVE_DISABLED_HINT;
  const next = prompt.latest.version + 1;

  async function save() {
    if (disabledHint !== null || Object.keys(errors).length > 0) {
      return;
    }
    setFailure(null);
    try {
      const created = await create.mutateAsync({
        template: draft.template,
        model_config: advice,
        commit_message: draft.commitMessage.trim() || null,
      });
      toast.success(`Saved v${String(created.version)} of ${prompt.name}.`);
      void navigate({ ...detail, search: { v: created.version } });
    } catch (error) {
      setFailure(promptErrorMessage(error));
    }
  }

  const template = draft.template;
  const chat = template.kind === "chat";
  const variableCount = inspection.variables?.length ?? 0;
  const subtitle =
    template.kind === "chat"
      ? `${pluralize(template.messages.length, "message")} · edited from v${String(base.version)}`
      : `Plain text · ${pluralize(variableCount, "variable")}`;
  return (
    <EditorFrame
      back={<BackToPrompt name={prompt.name} version={base.version} />}
      title={
        <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
          New version of {prompt.name}
          <KindChip kind={prompt.kind} />
        </span>
      }
      description={`Editing from v${String(base.version)}. Saving creates v${String(next)}; v${String(base.version)} stays as it is.`}
      onSubmit={() => void save()}
      main={
        <>
          <CardTitle title={chat ? "Messages" : "Template"} subtitle={subtitle} />
          <TemplateEditor
            template={draft.template}
            onChange={(template) => setDraft({ ...draft, template })}
            base={base.template}
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
          cancel={<Link {...detail}>Cancel</Link>}
        />
      }
    />
  );
}
