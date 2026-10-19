import type { ReactNode } from "react";

import { ErrorClassChip } from "@/components/error-class-chip";
import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import type { ErrorClass } from "@/lib/api";
import { formatCompact, formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The class chip beside the first error message; renders nothing when neither is known. */
export function TraceErrorLine({
  errorClass,
  message,
  className,
}: {
  errorClass: ErrorClass | null;
  message: string | null;
  className?: string;
}) {
  if (errorClass === null && !message) {
    return null;
  }
  return (
    <span className={cn("flex min-w-0 items-center gap-1.5", className)}>
      {errorClass !== null ? <ErrorClassChip errorClass={errorClass} /> : null}
      {message ? (
        <span title={message} className="min-w-0 truncate text-xs text-danger-text">
          {message}
        </span>
      ) : null}
    </span>
  );
}

/** Figma "Traces/Mono badge": a model name chip in JetBrains Mono. */
export function MonoBadge({ value, className }: { value: string; className?: string }) {
  return (
    <span
      title={value}
      className={cn(
        "inline-flex min-w-0 items-center rounded-full border border-border bg-surface-muted px-2 py-0.5 font-mono text-label text-foreground",
        className,
      )}
    >
      <span className="truncate">{value}</span>
    </span>
  );
}

interface OverflowListProps {
  values: string[];
  /** How many values to show before collapsing the rest into "+N". */
  max: number;
  renderValue: (value: string) => ReactNode;
}

/** A row of chips that collapses extra values into a "+N" chip with a tooltip. */
function OverflowList({ values, max, renderValue }: OverflowListProps) {
  const visible = values.slice(0, max);
  const hidden = values.slice(max);
  return (
    <span className="flex min-w-0 items-center gap-1">
      {visible.map((value) => (
        <span key={value} className="flex min-w-0">
          {renderValue(value)}
        </span>
      ))}
      {hidden.length > 0 ? (
        <Tooltip content={hidden.join(", ")}>
          <Badge
            variant="outline"
            tabIndex={0}
            className="cursor-help"
            aria-label={`${hidden.length} more: ${hidden.join(", ")}`}
          >
            +{hidden.length}
          </Badge>
        </Tooltip>
      ) : null}
    </span>
  );
}

export function ModelBadges({ models, max = 1 }: { models: string[]; max?: number }) {
  if (models.length === 0) {
    return <span className="text-xs text-muted-foreground">No LLM calls</span>;
  }
  return (
    <OverflowList values={models} max={max} renderValue={(model) => <MonoBadge value={model} />} />
  );
}

export function TagBadges({ tags, max = 2 }: { tags: string[]; max?: number }) {
  if (tags.length === 0) {
    return null;
  }
  return (
    <OverflowList
      values={tags}
      max={max}
      renderValue={(tag) => (
        // Badges never shrink by default; a tag in a narrow cell must truncate instead.
        <Badge title={tag} className="min-w-0 shrink">
          <span className="truncate">{tag}</span>
        </Badge>
      )}
    />
  );
}

/** Exact below 10,000, compact above, so the column stays narrow. */
function formatTokenCount(value: number): string {
  const formatted = value < 10_000 ? formatInteger(value) : formatCompact(value);
  return formatted ?? "0";
}

/** "1,312 → 528" with the full wording for screen readers. */
export function TokenPair({ input, output }: { input: number; output: number }) {
  const full = `${formatInteger(input) ?? "0"} input → ${formatInteger(output) ?? "0"} output tokens`;
  return (
    <span title={full} className="whitespace-nowrap tabular">
      <span className="sr-only">{full}</span>
      <span aria-hidden>
        {formatTokenCount(input)} → {formatTokenCount(output)}
      </span>
    </span>
  );
}

/** "u_8f21 · s_42" in mono; either side may be missing. */
export function UserSession({
  userId,
  sessionId,
}: {
  userId: string | null;
  sessionId: string | null;
}) {
  if (userId === null && sessionId === null) {
    return <span className="text-muted-foreground">—</span>;
  }
  const title = [userId && `User ${userId}`, sessionId && `Session ${sessionId}`]
    .filter(Boolean)
    .join(" · ");
  return (
    <span title={title} className="block truncate font-mono text-label text-muted-foreground">
      {userId !== null ? (
        <>
          <span className="sr-only">User </span>
          <span>{userId}</span>
        </>
      ) : null}
      {userId !== null && sessionId !== null ? <span aria-hidden> · </span> : null}
      {sessionId !== null ? (
        <>
          <span className="sr-only">{userId !== null ? ", session " : "Session "}</span>
          <span>{sessionId}</span>
        </>
      ) : null}
    </span>
  );
}
