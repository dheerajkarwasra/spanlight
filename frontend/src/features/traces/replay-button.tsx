import { Link } from "@tanstack/react-router";
import { PencilLine, RotateCcw } from "lucide-react";
import { useId, type ReactNode } from "react";

import { DisabledReason } from "@/components/disabled-reason";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
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

interface ReplayButtonProps {
  traceId: string;
  span: Span;
  /**
   * The summary bar's one-row form: reasons go to the tooltip, and below the `@6xl` container
   * width the buttons are icon-only.
   */
  compact?: boolean;
}

/**
 * Figma "Span panel / Replay button": opens the playground's replay column for an LLM span, or
 * says why it can't. Other span kinds have nothing to replay and show no button.
 */
export function ReplayButton({ traceId, span, compact = false }: ReplayButtonProps) {
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
  const visibleReason = !compact && state.reason !== null && !TOOLTIP_ONLY.has(state.reason);
  const describedBy = visibleReason ? reasonId : undefined;
  const replay = `${traceId}:${span.span_id}`;
  const label = <ButtonLabel compact={compact}>{state.label}</ButtonLabel>;

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {state.disabled ? (
          <DisabledButton
            state={state}
            loading={source.isPending && state.reason === null}
            describedBy={describedBy}
            label={label}
          />
        ) : (
          <WithTooltip content={compact ? (state.reason ?? state.label) : null}>
            <PlaygroundLink replay={replay} describedBy={describedBy} label={state.label}>
              <RotateCcw aria-hidden />
              {label}
            </PlaygroundLink>
          </WithTooltip>
        )}
        {state.edit ? (
          <WithTooltip content={compact ? EDIT_AND_REPLAY_LABEL : null}>
            <PlaygroundLink replay={replay} edit label={EDIT_AND_REPLAY_LABEL}>
              <PencilLine aria-hidden />
              <ButtonLabel compact={compact}>{EDIT_AND_REPLAY_LABEL}</ButtonLabel>
            </PlaygroundLink>
          </WithTooltip>
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

/** The button text; in the compact form it hides below the `@6xl` container width. */
function ButtonLabel({ compact, children }: { compact: boolean; children: string }) {
  return compact ? <span className="hidden @6xl:inline">{children}</span> : <>{children}</>;
}

function WithTooltip({ content, children }: { content: string | null; children: ReactNode }) {
  return content === null ? children : <Tooltip content={content}>{children}</Tooltip>;
}

interface DisabledButtonProps {
  state: ReplayButtonState;
  loading: boolean;
  describedBy: string | undefined;
  label: ReactNode;
}

function DisabledButton({ state, loading, describedBy, label }: DisabledButtonProps) {
  const button = (
    <Button size="sm" disabled loading={loading} aria-label={state.label}>
      <RotateCcw aria-hidden />
      {label}
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
  /** The accessible name, kept when the compact form hides the text. */
  label: string;
  children: ReactNode;
}

/** A small pill button linking to the playground's replay column for this span. */
function PlaygroundLink({
  replay,
  edit = false,
  describedBy,
  label,
  children,
  ...props
}: PlaygroundLinkProps) {
  const { orgId, projectId } = useProjectParams();
  const search: PlaygroundSearch = edit ? { replay, edit: true } : { replay };
  return (
    <Button asChild size="sm" variant={edit ? "ghost" : "secondary"} {...props}>
      <Link
        aria-label={label}
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
