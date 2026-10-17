import { Link } from "@tanstack/react-router";
import { PencilLine, RotateCcw } from "lucide-react";
import { useId, type ReactNode } from "react";

import { DisabledReason } from "@/components/disabled-reason";
import { Button } from "@/components/ui/button";
import type { PlaygroundSearch } from "@/features/playground/search";
import { useProjectParams, usePermission } from "@/features/shell";
import { isApiError, type Span } from "@/lib/api";
import { parseMoney } from "@/lib/format";

import {
  EDIT_AND_REPLAY_LABEL,
  REPLAY_OFF_REASON,
  VIEWER_REASON,
  replayButtonState,
  type ReplayButtonState,
} from "./replay-button-state";
import { useReplayBudgetQuery, useReplaySourceQuery } from "./replay-queries";

const SOURCE_ERROR_REASON =
  "Couldn't check whether this span can be replayed. Reload to try again.";

/** Reasons that are not about this span: shown in the tooltip only, not as a line under it. */
const TOOLTIP_ONLY = new Set([VIEWER_REASON, REPLAY_OFF_REASON]);

function isNotConfigured(error: unknown): boolean {
  return isApiError(error) && error.code === "NOT_CONFIGURED";
}

/**
 * Figma "Span panel / Replay button": opens the playground's replay column for an LLM span, or
 * says why it can't. Other span kinds have nothing to replay and show no button.
 */
export function ReplayButton({ traceId, span }: { traceId: string; span: Span }) {
  const isLlm = span.kind === "llm";
  const canRun = usePermission("experiments:run");
  const source = useReplaySourceQuery(traceId, span.span_id, { enabled: isLlm });
  const budget = useReplayBudgetQuery({ enabled: isLlm });
  const reasonId = useId();
  if (!isLlm) {
    return null;
  }
  const replayOff =
    isNotConfigured(source.error) ||
    isNotConfigured(budget.error) ||
    parseMoney(budget.data?.monthly_budget_usd) === 0;
  let state = replayButtonState(source.data, canRun, replayOff);
  if (source.isError && state.disabled && state.reason === null) {
    state = { ...state, reason: SOURCE_ERROR_REASON };
  }
  const visibleReason = state.reason !== null && !TOOLTIP_ONLY.has(state.reason);
  const replay = `${traceId}:${span.span_id}`;

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {state.disabled ? (
          <DisabledButton
            state={state}
            loading={source.isPending && state.reason === null}
            describedBy={visibleReason ? reasonId : undefined}
          />
        ) : (
          <PlaygroundLink replay={replay} describedBy={visibleReason ? reasonId : undefined}>
            <RotateCcw aria-hidden />
            {state.label}
          </PlaygroundLink>
        )}
        {state.edit ? (
          <PlaygroundLink replay={replay} edit>
            <PencilLine aria-hidden />
            {EDIT_AND_REPLAY_LABEL}
          </PlaygroundLink>
        ) : null}
      </div>
      {visibleReason ? (
        <p id={reasonId} className="text-xs leading-5 text-muted-foreground">
          {state.reason}
        </p>
      ) : null}
    </div>
  );
}

interface DisabledButtonProps {
  state: ReplayButtonState;
  loading: boolean;
  describedBy: string | undefined;
}

function DisabledButton({ state, loading, describedBy }: DisabledButtonProps) {
  const button = (
    <Button size="sm" disabled loading={loading}>
      <RotateCcw aria-hidden />
      {state.label}
    </Button>
  );
  if (state.reason === null) {
    return button;
  }
  return (
    <DisabledReason reason={state.reason} describedBy={describedBy}>
      {button}
    </DisabledReason>
  );
}

interface PlaygroundLinkProps {
  /** `<trace_id>:<span_id>`, the playground's `replay` search param. */
  replay: string;
  /** Open the replay column with the request already in edit mode. */
  edit?: boolean;
  describedBy?: string | undefined;
  children: ReactNode;
}

/** A small pill button linking to the playground's replay column for this span. */
function PlaygroundLink({ replay, edit = false, describedBy, children }: PlaygroundLinkProps) {
  const { orgId, projectId } = useProjectParams();
  const search: PlaygroundSearch = edit ? { replay, edit: true } : { replay };
  return (
    <Button asChild size="sm" variant={edit ? "ghost" : "secondary"}>
      <Link
        to="/$orgId/$projectId/playground"
        params={{ orgId, projectId }}
        search={search}
        aria-describedby={describedBy}
      >
        {children}
      </Link>
    </Button>
  );
}
