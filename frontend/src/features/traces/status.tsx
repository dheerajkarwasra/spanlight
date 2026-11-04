import { CircleAlert, CircleCheck, Info } from "lucide-react";

import { StatusDot } from "@/components/status-dot";
import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import type { SpanStatus } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { rootStatusLabel } from "./root-status";

function errorCountLabel(errorCount: number): string {
  return errorCount === 1 ? "1 error" : `${formatInteger(errorCount) ?? errorCount} errors`;
}

interface TraceStatusIconProps {
  errorCount: number;
  rootReceived: boolean | null;
}

/** The summary bar's status icon: errors first, then a missing or unknown root, then OK. */
export function TraceStatusIcon({ errorCount, rootReceived }: TraceStatusIconProps) {
  const rootLabel = rootStatusLabel(rootReceived);
  if (errorCount === 0 && rootLabel) {
    return (
      <Tooltip content={rootLabel}>
        <span className="inline-flex">
          <Info aria-hidden className="size-4 text-muted-foreground" />
          <span className="sr-only">{rootLabel}</span>
        </span>
      </Tooltip>
    );
  }
  if (errorCount > 0) {
    const label = errorCountLabel(errorCount);
    return (
      <Tooltip content={label}>
        <span className="inline-flex">
          <CircleAlert aria-hidden className="size-4 text-danger" />
          <span className="sr-only">Error: {label}</span>
        </span>
      </Tooltip>
    );
  }
  return (
    <span className="inline-flex">
      <CircleCheck aria-hidden className="size-4 text-success" />
      <span className="sr-only">OK</span>
    </span>
  );
}

interface TraceStatusDotProps {
  errorCount: number;
  /** Without errors, a missing or unknown root shows a neutral icon: the caller names it in text. */
  rootReceived: boolean | null;
  /** The earliest failed span's message, shown in the tooltip. */
  errorMessage?: string | null;
  /** Set inside a link or button, where a focusable tooltip trigger isn't allowed. */
  static?: boolean;
}

/**
 * List-row status (Figma "Status dot"): green for OK, rose for failed, a grey info icon while the
 * root is missing or unknown. A failed trace's dot is focusable and its tooltip shows the first
 * error message.
 */
export function TraceStatusDot({
  errorCount,
  rootReceived,
  errorMessage = null,
  static: isStatic = false,
}: TraceStatusDotProps) {
  if (errorCount === 0 && rootReceived !== true) {
    return (
      <Info aria-hidden strokeWidth={2.25} className="size-3 shrink-0 text-muted-foreground" />
    );
  }
  if (errorCount === 0) {
    return <StatusDot state="ok" label="OK" />;
  }

  const summary = errorCountLabel(errorCount);
  const label = errorMessage ? `Failed, ${summary}: ${errorMessage}` : `Failed, ${summary}`;
  if (isStatic) {
    return <StatusDot state="error" label={label} />;
  }

  return (
    <Tooltip
      content={
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold">{summary}</span>
          {errorMessage ? <span className="font-normal break-words">{errorMessage}</span> : null}
        </span>
      }
    >
      <span
        role="img"
        tabIndex={0}
        aria-label={label}
        className="inline-flex size-4 cursor-help items-center justify-center rounded-full"
      >
        <StatusDot state="error" />
      </span>
    </Tooltip>
  );
}

/** Trace header badge: "N errors" in rose, or "OK" in green. */
export function TraceStatusBadge({ errorCount }: { errorCount: number }) {
  if (errorCount > 0) {
    return <Badge variant="danger">{errorCountLabel(errorCount)}</Badge>;
  }
  return <Badge variant="success">OK</Badge>;
}

export function SpanStatusBadge({ status }: { status: SpanStatus }) {
  if (status === "error") {
    return <Badge variant="danger">Error</Badge>;
  }
  if (status === "ok") {
    return <Badge variant="success">OK</Badge>;
  }
  return (
    <Tooltip content="The instrumentation didn't set a status">
      <Badge variant="neutral" tabIndex={0} className="cursor-help">
        Unset
      </Badge>
    </Tooltip>
  );
}
