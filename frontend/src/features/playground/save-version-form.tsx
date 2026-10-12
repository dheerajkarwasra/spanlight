import { useId, useState } from "react";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogFooter } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { isApiError, type PromptListItem } from "@/lib/api";

import { describeTemplateError, inspectTemplate } from "@/lib/prompt-template";

import { filledMessages, messagesTemplate, modelAdvice, type ColumnState } from "./config-state";
import { usePromptFetcher, useSaveVersion, usePromptsQuery } from "./playground-queries";

export interface SavedVersion {
  name: string;
  version: number;
}

function saveProblem(error: unknown, name: string): string {
  if (isApiError(error) && error.code === "NO_CHANGES") {
    return `These messages equal the latest version of ${name}. Change them to save a new version.`;
  }
  if (isApiError(error) && error.code === "KIND_MISMATCH") {
    return `${name} is a text prompt. Playground messages can only join a chat prompt.`;
  }
  if (isApiError(error) && error.status === 404) {
    return `${name} no longer exists. Pick another prompt.`;
  }
  return isApiError(error) ? error.message : "Couldn't save the version. Try again.";
}

/** Why the column's messages can't become a version yet; null when they can. */
function templateBlock(column: ColumnState): string | null {
  if (filledMessages(column).length === 0) {
    return "Write at least one message to save it as a version.";
  }
  const { problem } = inspectTemplate(messagesTemplate(column));
  return problem === null ? null : describeTemplateError(problem);
}

/** The messages as the saved template will read them, with `{{variables}}` picked out in violet. */
function TemplatePreview({ column, letter }: { column: ColumnState; letter: string }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-tile bg-surface-muted p-4">
      <p className="text-overline text-subtle-foreground uppercase">
        Template from column {letter}
      </p>
      <pre className="font-mono text-xs leading-5 [overflow-wrap:anywhere] whitespace-pre-wrap text-foreground">
        {filledMessages(column).map((message, index) => (
          <span key={index} className="block">
            {message.role}:{" "}
            {message.content.split(/(\{\{[\s\S]*?\}\})/).map((part, at) =>
              at % 2 === 1 ? (
                <span key={at} className="text-accent">
                  {part}
                </span>
              ) : (
                part
              ),
            )}
          </span>
        ))}
      </pre>
    </div>
  );
}

interface SaveVersionFormProps {
  column: ColumnState;
  letter: string;
  onPendingChange: (pending: boolean) => void;
  onSaved: (saved: SavedVersion) => void;
}

/** Figma "Playground — Save as version": the body of the dialog. */
export function SaveVersionForm({
  column,
  letter,
  onPendingChange,
  onSaved,
}: SaveVersionFormProps) {
  const prompts = usePromptsQuery();
  const save = useSaveVersion();
  const fetchPrompt = usePromptFetcher();
  const hintId = useId();
  const configId = useId();
  const [picked, setPicked] = useState<string | null>(null);
  const [withConfig, setWithConfig] = useState(true);
  const [commit, setCommit] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const chatPrompts: PromptListItem[] = (prompts.data ?? []).filter((item) => item.kind === "chat");
  const target = chatPrompts.find((item) => item.name === (picked ?? chatPrompts[0]?.name));

  const blocked = templateBlock(column);

  async function submit() {
    if (!target || blocked !== null) {
      return;
    }
    setProblem(null);
    onPendingChange(true);
    try {
      // Unchecked means "keep the prompt's advice": carry the latest version's config forward,
      // because an omitted config would store an empty one.
      const config = withConfig
        ? modelAdvice(column)
        : (await fetchPrompt(target.name)).latest.model_config;
      const created = await save.mutateAsync({
        name: target.name,
        input: {
          template: messagesTemplate(column),
          model_config: config,
          commit_message: commit.trim() === "" ? null : commit.trim(),
        },
      });
      onSaved({ name: target.name, version: created.version });
    } catch (error) {
      setProblem(saveProblem(error, target.name));
    } finally {
      onPendingChange(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      {prompts.isPending ? <Skeleton className="h-[46px] w-full rounded-input" /> : null}
      {prompts.isError ? (
        <Notice tone="danger" role="alert">
          Couldn&apos;t load prompts. Close this and try again.
        </Notice>
      ) : null}
      {prompts.isSuccess && chatPrompts.length === 0 ? (
        <Notice>
          No chat prompts yet. Create one in Prompts first; a version written here joins an existing
          chat prompt.
        </Notice>
      ) : null}
      {target ? (
        <FormField
          label="Prompt"
          hint={`Creates v${target.latest_version + 1} of ${target.name}. The kind stays chat.`}
        >
          <FieldSelect
            value={target.name}
            onChange={setPicked}
            options={chatPrompts.map((item) => ({ value: item.name, label: item.name }))}
          />
        </FormField>
      ) : null}
      <TemplatePreview column={column} letter={letter} />
      <div className="flex items-center gap-2.5">
        <input
          id={configId}
          type="checkbox"
          checked={withConfig}
          onChange={(event) => setWithConfig(event.target.checked)}
          className="size-4 rounded-checkbox accent-accent"
        />
        <label htmlFor={configId} className="text-label text-foreground">
          Also save the model and parameters as the version config (unchecked keeps the
          prompt&apos;s current config)
        </label>
      </div>
      <FormField label="Commit message">
        <Textarea
          rows={2}
          value={commit}
          maxLength={500}
          onChange={(event) => setCommit(event.target.value)}
          placeholder={`What changed in column ${letter}?`}
        />
      </FormField>
      {problem ? (
        <Notice tone="danger" role="alert">
          {problem}
        </Notice>
      ) : null}
      <DialogFooter>
        <DialogClose asChild>
          <Button variant="secondary" type="button" disabled={save.isPending}>
            Cancel
          </Button>
        </DialogClose>
        <Button
          variant="primary"
          type="submit"
          loading={save.isPending}
          disabled={!target || blocked !== null}
          aria-describedby={blocked === null ? undefined : hintId}
        >
          Save version
        </Button>
      </DialogFooter>
      {blocked === null ? null : (
        <p id={hintId} className="text-xs font-medium text-muted-foreground">
          {blocked}
        </p>
      )}
    </form>
  );
}
