import { ChevronDown, ChevronUp } from "lucide-react";
import { useId, useState } from "react";

import { formatInteger } from "@/lib/format";

const SNAPSHOT_NOTE =
  "As the provider sent them, shown as text. Spanlight keeps 29 known rate-limit headers and never auth headers.";

/**
 * Figma "Span detail/Provider error block", rate-limit headers: a disclosure, open by default,
 * over the header snapshot as name/value pairs in mono. Values are rendered as text only.
 */
export function RatelimitSnapshot({ rows }: { rows: readonly [string, string][] }) {
  const [open, setOpen] = useState(true);
  const listId = useId();

  if (rows.length === 0) {
    return (
      <p className="text-xs font-medium text-muted-foreground">
        No rate-limit headers were captured for this span.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => {
          setOpen((value) => !value);
        }}
        className="-mx-1 flex min-h-11 w-fit items-center gap-1.5 rounded-sm px-1 text-sm font-semibold text-foreground sm:min-h-8"
      >
        {open ? (
          <ChevronUp aria-hidden className="size-4" />
        ) : (
          <ChevronDown aria-hidden className="size-4" />
        )}
        Rate-limit headers · {formatInteger(rows.length)}
      </button>
      <div id={listId} hidden={!open} className="flex flex-col gap-2">
        <ul
          aria-label="Rate-limit headers"
          className="flex flex-col gap-2 rounded-input bg-surface px-3 py-3"
        >
          {rows.map(([name, value]) => (
            <li key={name} className="flex min-w-0 flex-col font-mono text-label">
              <span className="break-all text-muted-foreground">{name}</span>
              <span className="break-all text-foreground">{value}</span>
            </li>
          ))}
        </ul>
        <p className="text-xs font-medium text-muted-foreground">{SNAPSHOT_NOTE}</p>
      </div>
    </div>
  );
}
