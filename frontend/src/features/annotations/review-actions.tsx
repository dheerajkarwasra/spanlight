import { Link } from "@tanstack/react-router";
import { Database, Info, RotateCcw } from "lucide-react";
import { useId } from "react";

import { DisabledReason } from "@/components/disabled-reason";
import { Button } from "@/components/ui/button";

import { ActionRow } from "./key-chip";
import { VIEWER_REASON, type ReviewActionsState } from "./use-review-actions";

interface ReviewActionsProps {
  actions: ReviewActionsState;
  /** True while a save, skip or claim runs. */
  busy: boolean;
}

/**
 * Figma "Annotate / actions": "Replay failing span" (opens the playground's replay column in a
 * new tab, so the claim stays) and "Save as dataset item", each with its key. Replay is disabled,
 * with the reason, for a viewer and for a trace without an LLM span.
 */
export function ReviewActions({ actions, busy }: ReviewActionsProps) {
  const { trace, replayTo, replayReason } = actions;
  const reasonId = useId();
  // The role reason stays in the tooltip; a reason about this trace is also written out.
  const visibleReason = replayReason !== null && replayReason !== VIEWER_REASON;
  const replayButton = (
    <Button
      className="w-full"
      disabled
      loading={trace === undefined && replayReason === null}
      aria-keyshortcuts="R"
    >
      <RotateCcw aria-hidden />
      Replay failing span
    </Button>
  );
  return (
    <>
      <ActionRow keyHint="R">
        {replayTo !== null ? (
          <Button className="flex-1" aria-keyshortcuts="R" asChild>
            <Link {...replayTo} target="_blank">
              <RotateCcw aria-hidden />
              Replay failing span
              <span className="sr-only">(opens in a new tab)</span>
            </Link>
          </Button>
        ) : (
          <div className="min-w-0 flex-1 [&>span]:w-full">
            {replayReason === null ? (
              replayButton
            ) : (
              <DisabledReason
                reason={replayReason}
                describedBy={visibleReason ? reasonId : undefined}
              >
                {replayButton}
              </DisabledReason>
            )}
          </div>
        )}
      </ActionRow>
      {visibleReason ? (
        <p id={reasonId} className="flex gap-1.5 text-xs font-medium text-muted-foreground">
          <Info aria-hidden className="mt-px size-3.5 shrink-0" />
          {replayReason}
        </p>
      ) : null}
      <ActionRow keyHint="D">
        <Button
          className="flex-1"
          disabled={busy || trace === undefined}
          aria-keyshortcuts="D"
          onClick={() => actions.setSaveOpen(true)}
        >
          <Database aria-hidden />
          Save as dataset item
        </Button>
      </ActionRow>
    </>
  );
}
