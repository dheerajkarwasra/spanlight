import { Link } from "@tanstack/react-router";
import { useState, type ReactElement } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { ErrorState } from "@/components/error-state";
import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import { formatInteger } from "@/lib/format";
import { checkScoreInput, COMMENT_LIMIT } from "@/lib/score-format";

import { scoreErrorMessage } from "./score-errors";
import { ScoreValueField } from "./score-value-field";
import { newScoreId, useCreateScore, useScoreConfigsQuery } from "./scores-queries";

interface AddScoreDialogProps {
  traceId: string;
  /** Scores one span of the trace instead of the whole trace. */
  spanId?: string | null;
  trigger: ReactElement;
}

/**
 * Figma "Add score dialog" (numeric with its error, boolean, categorical): a person scores the
 * trace, or one of its spans, by hand. The value is checked against the score's config before it
 * is sent, with the rules the API applies.
 */
export function AddScoreDialog({ traceId, spanId = null, trigger }: AddScoreDialogProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !pending) setOpen(next);
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[520px] overflow-y-auto rounded-card p-6 sm:p-7"
        >
          <AddScoreForm
            traceId={traceId}
            spanId={spanId}
            onPendingChange={setPending}
            onDone={() => {
              setOpen(false);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

interface AddScoreFormProps {
  traceId: string;
  spanId: string | null;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
}

function AddScoreForm({ traceId, spanId, onPendingChange, onDone }: AddScoreFormProps) {
  const configsQuery = useScoreConfigsQuery();
  const create = useCreateScore(traceId);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [comment, setComment] = useState("");
  const [checked, setChecked] = useState(false);
  // One id per dialog, so retrying after a lost response cannot store the score twice.
  const [scoreId] = useState(newScoreId);
  const config = configsQuery.data?.find((candidate) => candidate.name === name) ?? null;
  const check = config ? checkScoreInput(config, value) : null;
  const valueError = checked && check && !check.ok ? check.error : null;
  const target = spanId ? "span" : "trace";

  function submit() {
    setChecked(true);
    if (!config || !check?.ok) {
      return;
    }
    onPendingChange(true);
    create.mutate(
      {
        id: scoreId,
        span_id: spanId,
        name: config.name,
        ...check.input,
        comment: comment.trim() === "" ? null : comment.trim(),
      },
      {
        onSuccess: () => {
          toast.success(`Added a ${config.name} score to this ${target}.`);
          onDone();
        },
        onSettled: () => {
          onPendingChange(false);
        },
      },
    );
  }

  return (
    <form
      noValidate
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <DialogHeading
        title="Add score"
        description={`Score this ${target} by hand. The score is saved as Human, under your name.`}
        showClose={create.isPending ? "disabled" : true}
      />
      <ConfigField
        query={configsQuery}
        name={name}
        onChange={(next) => {
          setName(next);
          setValue("");
          setChecked(false);
        }}
        missing={checked && !config}
      />
      {config ? (
        <ScoreValueField
          config={config}
          value={value}
          onChange={setValue}
          error={valueError}
          disabled={create.isPending}
        />
      ) : null}
      <FormField
        label="Comment (optional)"
        labelAction={
          <span className="text-xs text-muted-foreground tabular">
            {formatInteger(comment.length)} of {formatInteger(COMMENT_LIMIT)}
          </span>
        }
      >
        <Textarea
          value={comment}
          maxLength={COMMENT_LIMIT}
          rows={3}
          disabled={create.isPending}
          onChange={(event) => {
            setComment(event.target.value);
          }}
        />
      </FormField>
      {create.error ? (
        <Notice tone="danger" role="alert">
          {scoreErrorMessage(create.error, "add")}
        </Notice>
      ) : null}
      <div className="flex justify-end gap-2.5">
        <DialogClose asChild>
          <Button disabled={create.isPending}>Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={create.isPending}>
          Add score
        </Button>
      </div>
    </form>
  );
}

interface ConfigFieldProps {
  query: ReturnType<typeof useScoreConfigsQuery>;
  name: string;
  onChange: (name: string) => void;
  missing: boolean;
}

/** The score to add: one of the project's score configs. */
function ConfigField({ query, name, onChange, missing }: ConfigFieldProps) {
  const { orgId, projectId } = useProjectParams();
  if (query.isPending) {
    return (
      <Skeleton role="status" aria-label="Loading scores" className="h-[70px] rounded-input" />
    );
  }
  if (query.isError) {
    return (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load the score configs"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  if (query.data.length === 0) {
    return (
      <Notice>
        This project has no score configs yet. Create one in{" "}
        <Link
          to="/$orgId/$projectId/settings/project"
          params={{ orgId, projectId }}
          className="font-semibold text-accent underline-offset-4 hover:underline"
        >
          project settings
        </Link>
        , or send a score from the SDK, then add it here.
      </Notice>
    );
  }
  return (
    <FormField label="Score" error={missing ? "Pick a score." : undefined}>
      <FieldSelect
        value={name}
        onChange={onChange}
        placeholder="Pick a score"
        options={query.data.map((config) => ({ value: config.name, label: config.name }))}
      />
    </FormField>
  );
}
