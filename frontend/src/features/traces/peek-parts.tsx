import { ChevronDown, ChevronUp, SearchX } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { isApiError } from "@/lib/api";

import type { useTraceQuery } from "./trace-queries";
import { TraceView, TraceViewSkeleton, type PeekSelection } from "./trace-view";

interface PeekBodyProps {
  traceQuery: ReturnType<typeof useTraceQuery>;
  selection: PeekSelection;
  onSelectionChange: (next: PeekSelection) => void;
  scrollElement: HTMLElement | null;
  linkHref: string;
}

/** The peeked trace's view, or its loading, not-found and error states. */
export function PeekBody({ traceQuery, ...view }: PeekBodyProps) {
  if (traceQuery.isPending) {
    return (
      <div aria-busy>
        <span className="sr-only" role="status">
          Loading trace
        </span>
        <TraceViewSkeleton />
      </div>
    );
  }
  if (traceQuery.isError) {
    return isApiError(traceQuery.error) && traceQuery.error.isNotFound ? (
      <EmptyState
        icon={SearchX}
        title="Trace not found"
        description="It may have been deleted by the retention policy, or the ID is wrong."
      />
    ) : (
      <ErrorState
        error={traceQuery.error}
        title="Couldn't load this trace"
        onRetry={() => {
          void traceQuery.refetch();
        }}
      />
    );
  }
  return (
    <TraceView trace={traceQuery.data} spans={traceQuery.data.spans} variant="peek" {...view} />
  );
}

interface StepButtonProps {
  shortcut: "j" | "k";
  disabled: boolean;
  onClick: () => void;
}

/**
 * Previous (k) or next (j) trace in the list; `aria-keyshortcuts` names the key. At an end it is
 * `aria-disabled`, not `disabled`, so the focused button keeps focus inside the sheet.
 */
export function StepButton({ shortcut, disabled, onClick }: StepButtonProps) {
  const next = shortcut === "j";
  const label = next ? "Next trace" : "Previous trace";
  const Icon = next ? ChevronDown : ChevronUp;
  return (
    <Tooltip content={`${label} (${shortcut.toUpperCase()})`}>
      <Button
        size="icon-sm"
        aria-label={label}
        aria-keyshortcuts={shortcut}
        aria-disabled={disabled}
        className="aria-disabled:cursor-default aria-disabled:opacity-50"
        onClick={disabled ? undefined : onClick}
      >
        <Icon aria-hidden />
      </Button>
    </Tooltip>
  );
}
