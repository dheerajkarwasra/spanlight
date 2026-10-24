import { Link } from "@tanstack/react-router";

import { useProjectParams } from "@/features/shell/project-context";
import type { StepSpan } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { DiffItem } from "./compare-diff";

/** One side's lines: removed or added ones tinted, with a +/− mark and a spoken prefix. */
export function DiffColumn({ label, items }: { label: string; items: DiffItem[] }) {
  if (items.length === 0) {
    return <p className="text-xs text-muted-foreground">Not captured.</p>;
  }
  return (
    <ol aria-label={label} className="flex flex-col font-mono text-xs leading-5">
      {items.map((item, index) =>
        item.kind === "gap" ? (
          <li key={index} className="px-1.5 text-subtle-foreground">
            ⋯ {item.count} unchanged {item.count === 1 ? "line" : "lines"}
          </li>
        ) : (
          <li
            key={index}
            className={cn(
              "flex gap-1.5 rounded-sm px-1.5",
              item.kind === "added" && "bg-success-subtle",
              item.kind === "removed" && "bg-danger-subtle",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "w-3 shrink-0",
                item.kind === "added" && "text-success",
                item.kind === "removed" && "text-danger-text",
              )}
            >
              {item.kind === "added" ? "+" : item.kind === "removed" ? "−" : ""}
            </span>
            <span className="min-w-0 flex-1 [overflow-wrap:anywhere] whitespace-pre-wrap text-foreground">
              {item.kind === "same" ? null : (
                <span className="sr-only">{item.kind === "added" ? "Added: " : "Removed: "}</span>
              )}
              {item.text}
            </span>
          </li>
        ),
      )}
    </ol>
  );
}

interface CutSide {
  label: string;
  traceId: string;
  span: StepSpan | undefined;
}

/** Shown when a side passed the payload guard: the diff covers its first 16 KB only. */
export function CutNote({ sides }: { sides: CutSide[] }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
      Showing the first 16 KB of each side; open the step for all.
      {sides.map(({ label, traceId, span }) =>
        span ? (
          <Link
            key={label}
            to="/$orgId/$projectId/traces/$traceId"
            params={{ orgId, projectId, traceId }}
            search={{ span: span.span_id }}
            className="font-medium text-foreground underline underline-offset-2"
          >
            Open in {label}
          </Link>
        ) : null,
      )}
    </p>
  );
}
