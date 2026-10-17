import { Lock, Plus, X } from "lucide-react";
import { useId, useRef, useState, type ReactElement } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { ScoreConfig } from "@/lib/api";

import {
  FAILURE_CATEGORY_SCORE,
  NONE_CATEGORY,
  addCategoryProblem,
  initialCategories,
} from "./failure-category";
import { scoreConfigErrorMessage } from "./score-errors";
import { useSaveFailureCategories } from "./scores-queries";

interface FailureCategoriesDialogProps {
  /** The project's `failure_category` config to edit; without it the dialog creates it. */
  config?: ScoreConfig;
  trigger: ReactElement;
}

/**
 * Figma "Failure categories dialog": the categories reviewers label traces with, as a chip list
 * pre-filled with a starting list. `none` (a reviewed trace that passed) is locked in.
 */
export function FailureCategoriesDialog({ config, trigger }: FailureCategoriesDialogProps) {
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
          <CategoriesForm
            config={config}
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

interface CategoriesFormProps {
  config: ScoreConfig | undefined;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
}

function CategoriesForm({ config, onPendingChange, onDone }: CategoriesFormProps) {
  const inputId = useId();
  const problemId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [list, setList] = useState<string[]>(() => initialCategories(config));
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const save = useSaveFailureCategories(config);

  function add() {
    const text = draft.trim();
    const reason = addCategoryProblem(text, list);
    setProblem(reason);
    if (reason === null) {
      setList([...list, text]);
      setDraft("");
    }
  }

  function submit() {
    if (list.length < 2) {
      setProblem("Add at least one category besides none.");
      return;
    }
    onPendingChange(true);
    save.mutate(list, {
      onSuccess: () => {
        toast.success(config ? "Saved the failure categories." : "Failure categories are set up.");
        onDone();
      },
      onSettled: () => {
        onPendingChange(false);
      },
    });
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
        title={config ? "Edit failure categories" : "Set up failure categories"}
        description="Reviewers label each trace they read with one of these. Start from this list and keep the categories your notes keep finding."
        showClose={save.isPending ? "disabled" : true}
      />
      <ul aria-label="Failure categories" className="flex flex-wrap gap-1.5">
        {list.map((item) => (
          <CategoryChip
            key={item}
            name={item}
            disabled={save.isPending}
            onRemove={() => {
              setList(list.filter((candidate) => candidate !== item));
              // The removed chip's button is gone; keep keyboard focus in the dialog.
              inputRef.current?.focus();
            }}
          />
        ))}
      </ul>
      <div className="flex flex-col gap-1.5">
        <label htmlFor={inputId} className="text-label font-semibold text-foreground">
          Add a category
        </label>
        <div className="flex gap-2">
          <Input
            ref={inputRef}
            id={inputId}
            value={draft}
            autoComplete="off"
            spellCheck={false}
            placeholder="e.g. wrong_tool_args"
            className="font-mono"
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? problemId : undefined}
            onChange={(event) => {
              setDraft(event.target.value);
              setProblem(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
          />
          <Button type="button" variant="secondary" onClick={add} disabled={save.isPending}>
            <Plus aria-hidden />
            Add
          </Button>
        </div>
        {problem ? (
          <p id={problemId} role="alert" className="text-xs font-medium text-danger-text">
            {problem}
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            none marks a trace that passed review; the rates count it, so it stays.
          </p>
        )}
      </div>
      {save.error ? (
        <Notice tone="danger" role="alert">
          {scoreConfigErrorMessage(save.error, FAILURE_CATEGORY_SCORE)}
        </Notice>
      ) : null}
      <div className="flex justify-end gap-2.5">
        <DialogClose asChild>
          <Button disabled={save.isPending}>Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={save.isPending}>
          {config ? "Save changes" : "Create categories"}
        </Button>
      </div>
    </form>
  );
}

interface CategoryChipProps {
  name: string;
  disabled: boolean;
  onRemove: () => void;
}

/** One category; `none` shows a lock instead of the remove button. */
function CategoryChip({ name, disabled, onRemove }: CategoryChipProps) {
  const locked = name === NONE_CATEGORY;
  return (
    <li className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-surface py-0.5 pr-1 pl-2.5 font-mono text-label text-foreground">
      <span className="truncate">{name}</span>
      {locked ? (
        <span className="flex size-6 items-center justify-center text-muted-foreground">
          <Lock aria-hidden className="size-3" />
          <span className="sr-only">(required, can't be removed)</span>
        </span>
      ) : (
        <button
          type="button"
          disabled={disabled}
          aria-label={`Remove ${name}`}
          onClick={onRemove}
          className="flex size-6 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-surface-hover hover:text-foreground"
        >
          <X aria-hidden className="size-3" />
        </button>
      )}
    </li>
  );
}
