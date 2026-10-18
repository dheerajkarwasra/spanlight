import { Link } from "@tanstack/react-router";
import { useId } from "react";

import { Tooltip } from "@/components/ui/tooltip";
import { useProjectParams } from "@/features/shell/project-context";

import { MetadataValue } from "./metadata-chips";
import { metadataFilterLabel, sortedPairs } from "./metadata-filter";

/**
 * Figma "Metadata block" of the trace header: every pair by key, each value a link to the trace
 * list filtered by it. On phones the pairs stack one per row and the whole 44 px row is the link.
 */
export function MetadataBlock({ metadata }: { metadata: Readonly<Record<string, string>> }) {
  const { orgId, projectId } = useProjectParams();
  const headingId = useId();
  const pairs = sortedPairs(metadata);

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2">
        <h2 id={headingId} className="text-sm font-semibold text-foreground">
          Metadata
        </h2>
        {pairs.length > 0 ? (
          <span className="text-xs font-medium text-muted-foreground">
            {pairs.length === 1 ? "1 key" : `${pairs.length} keys`}
          </span>
        ) : null}
      </div>
      {pairs.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No metadata on this trace. Set it with{" "}
          <code className="font-mono text-label">update_trace(metadata=…)</code>.
        </p>
      ) : (
        <ul className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:gap-x-2.5">
          {pairs.map(([key, value]) => {
            const label = `Show traces where ${metadataFilterLabel(key, value)}`;
            return (
              <li
                key={key}
                className="relative flex min-h-11 max-w-full min-w-0 items-center gap-2 rounded-md bg-surface-muted px-3 py-1.5 font-mono sm:min-h-0"
              >
                <span className="max-w-48 shrink-0 truncate text-xs text-muted-foreground">
                  {key}
                </span>
                <Tooltip content={label} caret="start">
                  {/* Below sm the link's ::before stretches over the row: the tap target is the row. */}
                  <Link
                    to="/$orgId/$projectId/traces"
                    params={{ orgId, projectId }}
                    search={{ metadata_key: key, metadata_value: value }}
                    aria-label={label}
                    className="flex min-h-6 min-w-0 items-center rounded-sm text-label text-accent before:absolute before:inset-0 before:rounded-md hover:underline sm:before:hidden"
                  >
                    <MetadataValue value={value} />
                  </Link>
                </Tooltip>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
