import { Info } from "lucide-react";
import { useId, type ReactElement } from "react";

import { Callout } from "@/components/callout";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Textarea } from "@/components/ui/input";

import { CardTitle } from "./card-title";
import { MAX_COMMIT_MESSAGE, type AdviceErrors, type Draft } from "./draft";
import { VariablesPanel } from "./variables-panel";

interface EditorSideProps {
  draft: Draft;
  onDraftChange: (draft: Draft) => void;
  variables: string[] | null;
  adviceErrors: AdviceErrors;
  /** Why Save is off, shown under it; null when it is on. */
  disabledHint: string | null;
  pending: boolean;
  failure: string | null;
  /** The Cancel link (a router `Link` reading "Cancel"), styled here as a button. */
  cancel: ReactElement;
}

/** The editor's right column: variables, model config, commit message and the save bar. */
export function EditorSide(props: EditorSideProps) {
  const { draft, onDraftChange, adviceErrors } = props;
  const set = (patch: Partial<Draft>) => onDraftChange({ ...draft, ...patch });
  return (
    <div className="flex min-w-0 flex-col gap-4 lg:gap-6">
      <VariablesPanel variables={props.variables} />
      <Card className="flex flex-col gap-4 p-5 sm:p-6">
        <CardTitle title="Model config" subtitle="Advice for clients that fetch this prompt" />
        <FormField label="Model" hint="Optional. A model id, e.g. claude-haiku-5-5.">
          <Input
            value={draft.model}
            spellCheck={false}
            autoComplete="off"
            className="font-mono text-[13px]"
            onChange={(event) => set({ model: event.target.value })}
          />
        </FormField>
        <div className="grid grid-cols-2 gap-3">
          <FormField label="Temperature" error={adviceErrors.temperature}>
            <Input
              inputMode="decimal"
              value={draft.temperature}
              onChange={(event) => set({ temperature: event.target.value })}
            />
          </FormField>
          <FormField label="Max tokens" error={adviceErrors.maxTokens}>
            <Input
              inputMode="numeric"
              value={draft.maxTokens}
              onChange={(event) => set({ maxTokens: event.target.value })}
            />
          </FormField>
        </div>
      </Card>
      <Card className="p-5 sm:p-6">
        <FormField
          label="Commit message"
          hint={`Optional. Up to ${String(MAX_COMMIT_MESSAGE)} characters.`}
        >
          <Textarea
            rows={3}
            maxLength={MAX_COMMIT_MESSAGE}
            value={draft.commitMessage}
            placeholder="Describe what changed (optional)"
            onChange={(event) => set({ commitMessage: event.target.value })}
          />
        </FormField>
      </Card>
      <SaveBar {...props} />
    </div>
  );
}

function SaveBar({ disabledHint, pending, failure, cancel }: EditorSideProps) {
  const hintId = useId();
  return (
    <div className="flex flex-col gap-2.5">
      {failure === null ? null : (
        <Callout tone="danger" role="alert">
          {failure}
        </Callout>
      )}
      <div className="flex flex-wrap gap-2.5">
        <Button
          type="submit"
          variant="primary"
          size="lg"
          disabled={disabledHint !== null}
          loading={pending}
          aria-describedby={disabledHint === null ? undefined : hintId}
        >
          Save version
        </Button>
        <Button size="lg" asChild>
          {cancel}
        </Button>
      </div>
      {disabledHint === null ? null : (
        <p
          id={hintId}
          className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground"
        >
          <Info aria-hidden className="size-3.5 shrink-0" />
          {disabledHint}
        </p>
      )}
    </div>
  );
}
