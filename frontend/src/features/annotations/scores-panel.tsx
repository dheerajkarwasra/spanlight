import { ChevronDown, CircleCheck, Info } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Callout } from "@/components/callout";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import type { ScoreConfig } from "@/lib/api";
import { COMMENT_LIMIT } from "@/lib/score-format";
import { cn } from "@/lib/utils";

import { KeyChip } from "./key-chip";
import { ScoreInput } from "./score-input";
import { activeChoiceScore, draftStatus, missingScores, setCount } from "./score-draft";
import type { useItemDraft } from "./use-item-draft";
import type { SaveAction, WorkNotice } from "./work-state";

/** The item's state and actions, from the work session. */
export interface WorkControls {
  notice: WorkNotice | null;
  /** The save running now, or `"claim"` while the next item loads. */
  busy: SaveAction | "claim" | null;
  onSubmit: () => void;
  onSkip: () => void;
  onRelease: () => void;
}

interface ScoresPanelProps {
  configs: readonly ScoreConfig[];
  instructions: string | null;
  draft: ReturnType<typeof useItemDraft>;
  work: WorkControls;
}

/**
 * The right card of work mode (Figma "Scores panel"): instructions, one input per score of the
 * queue, the comment, and Submit & next, Skip and Release. Submit stays disabled until every score
 * is set and valid. Each new item moves focus to the panel's heading, so keyboard and screen
 * reader users start the next item where they finished the last.
 */
export function ScoresPanel({ configs, instructions, draft, work }: ScoresPanelProps) {
  const { notice, busy, onSubmit, onSkip, onRelease } = work;
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (draft.itemId !== null) {
      headingRef.current?.focus({ preventScroll: true });
    }
  }, [draft.itemId]);
  const commentId = useId();
  const keyed = activeChoiceScore(configs, draft.values, draft.focused);
  const ready = missingScores(configs, draft.values).length === 0;
  return (
    <Card
      role="region"
      aria-label="Scores"
      className="flex w-full shrink-0 flex-col gap-4 p-5 sm:p-[22px] lg:w-[410px]"
    >
      <div className="flex items-center gap-2">
        <h2
          ref={headingRef}
          tabIndex={-1}
          className="text-base font-bold text-foreground outline-none"
        >
          Scores
        </h2>
        <span className="ml-auto text-xs font-semibold text-muted-foreground">
          {setCount(configs, draft.values)} of {configs.length} set
        </span>
      </div>
      {instructions ? <Instructions text={instructions} /> : null}
      {configs.map((config) => (
        <ScoreInput
          key={config.id}
          config={config}
          value={draft.values[config.name] ?? null}
          onChange={(value) => draft.setValue(config.name, value)}
          keyed={keyed?.name === config.name}
          onFocusWithin={() => draft.setFocused(config.name)}
        />
      ))}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center">
          <label htmlFor={commentId} className="flex-1 text-xs font-semibold text-foreground">
            Comment (optional)
          </label>
          <span className="text-2xs font-medium text-muted-foreground tabular">
            {draft.comment.length.toLocaleString()} of {COMMENT_LIMIT.toLocaleString()}
          </span>
        </div>
        <Textarea
          id={commentId}
          value={draft.comment}
          maxLength={COMMENT_LIMIT}
          placeholder="Anything the next reviewer should know"
          aria-describedby={`${commentId}-hint`}
          onFocus={() => draft.setFocused(null)}
          onChange={(event) => draft.setComment(event.target.value)}
          className="min-h-[72px]"
        />
        <p
          id={`${commentId}-hint`}
          className="flex gap-1.5 text-xs font-medium text-muted-foreground"
        >
          <Info aria-hidden className="mt-px size-3.5 shrink-0" />
          Shortcuts are paused while you type in this field.
        </p>
      </div>
      {notice === null ? null : (
        <Callout tone="danger" role="alert" title={notice.title}>
          {notice.message}
        </Callout>
      )}
      <div className="sticky bottom-0 -mx-1 flex flex-col gap-2 bg-surface px-1 py-1 lg:static">
        <ActionRow keyHint="↵">
          <Button
            variant="primary"
            className="flex-1"
            disabled={!ready || busy !== null}
            loading={busy === "complete"}
            aria-keyshortcuts="Enter"
            onClick={onSubmit}
          >
            Submit &amp; next
          </Button>
        </ActionRow>
        <ActionRow keyHint="S">
          <Button
            className="flex-1"
            disabled={busy !== null}
            loading={busy === "skip"}
            aria-keyshortcuts="S"
            onClick={onSkip}
          >
            Skip
          </Button>
        </ActionRow>
        <ActionRow keyHint={null}>
          <Button
            className="flex-1"
            disabled={busy !== null}
            loading={busy === "release"}
            onClick={onRelease}
          >
            Release
          </Button>
        </ActionRow>
      </div>
      <p className="flex gap-1.5 text-xs font-medium text-muted-foreground">
        {ready ? (
          <CircleCheck aria-hidden className="mt-px size-3.5 shrink-0 text-success" />
        ) : (
          <Info aria-hidden className="mt-px size-3.5 shrink-0" />
        )}
        {draftStatus(configs, draft.values)}
      </p>
    </Card>
  );
}

function ActionRow({ keyHint, children }: { keyHint: string | null; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2.5">
      {children}
      {keyHint === null ? (
        <span aria-hidden className="hidden size-6 sm:block" />
      ) : (
        <KeyChip className="hidden sm:inline-flex">{keyHint}</KeyChip>
      )}
    </div>
  );
}

function Instructions({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="rounded-input bg-surface-muted">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center gap-2 rounded-input px-3 py-2.5 text-left text-label font-semibold text-foreground"
      >
        <Info aria-hidden className="size-3.5 shrink-0" />
        <span className="flex-1">Instructions</span>
        <ChevronDown
          aria-hidden
          className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-180")}
        />
      </button>
      <p
        id={id}
        hidden={!open}
        className="px-3 pb-3 text-label whitespace-pre-wrap text-muted-foreground"
      >
        {text}
      </p>
    </div>
  );
}
