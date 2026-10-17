import { Link } from "@tanstack/react-router";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { metadataFilterLabel, promotedPairs, REDACTED, type MetadataPair } from "./metadata-filter";

/** A value as text, truncated, or the "redacted" badge for a value the server redacted. */
export function MetadataValue({ value }: { value: string }) {
  if (value === REDACTED) {
    return (
      <Badge variant="warning" size="sm" title={REDACTED}>
        redacted
      </Badge>
    );
  }
  return (
    <span title={value} className="truncate">
      {value}
    </span>
  );
}

const CHIP =
  "inline-flex min-w-0 items-center gap-1.5 rounded-full bg-surface-muted px-2.5 py-0.5 text-xs";

interface MetadataChipsProps {
  metadata: Readonly<Record<string, string>>;
  promoted: readonly string[];
  /** How many chips to show before folding the rest into "+N"; all of them when omitted. */
  max?: number;
  /** Each chip links to the list filtered by its pair; off inside a link such as a tile. */
  linked?: boolean;
  className?: string;
}

/**
 * Figma "Metadata chip": one per promoted key the trace carries, key in sans and value in mono.
 * A trace without any promoted key renders nothing.
 */
export function MetadataChips({
  metadata,
  promoted,
  max = Number.POSITIVE_INFINITY,
  linked = false,
  className,
}: MetadataChipsProps) {
  const pairs = promotedPairs(metadata, promoted);
  if (pairs.length === 0) {
    return null;
  }
  const hidden = pairs.slice(max);
  return (
    <span className={cn("flex min-w-0 items-center gap-1.5", className)}>
      {pairs
        .slice(0, max)
        .map((pair) =>
          linked ? <ChipLink key={pair[0]} pair={pair} /> : <Chip key={pair[0]} pair={pair} />,
        )}
      {hidden.length > 0 ? (
        <span
          title={hidden.map((pair) => metadataFilterLabel(...pair)).join(", ")}
          className="shrink-0 text-xs font-medium text-muted-foreground"
        >
          <span aria-hidden>+{hidden.length}</span>
          <span className="sr-only">
            {`${hidden.length} more: ${hidden.map(([key, value]) => `${key} ${value}`).join(", ")}`}
          </span>
        </span>
      ) : null}
    </span>
  );
}

function ChipBody({ pair: [key, value] }: { pair: MetadataPair }) {
  return (
    <>
      <span className="max-w-32 shrink-0 truncate font-medium text-muted-foreground">{key}</span>
      {/* Whitespace between flex items is not drawn; it keeps "key value" apart for readers. */}{" "}
      <span className="flex min-w-0 font-mono text-foreground">
        <MetadataValue value={value} />
      </span>
    </>
  );
}

function Chip({ pair }: { pair: MetadataPair }) {
  return (
    <span className={CHIP}>
      <ChipBody pair={pair} />
    </span>
  );
}

function ChipLink({ pair }: { pair: MetadataPair }) {
  const [key, value] = pair;
  return (
    // The list's other filters stay: the chip narrows what is on screen.
    <Link
      from="/$orgId/$projectId/traces"
      to="."
      search={(prev) => ({ ...prev, metadata_key: key, metadata_value: value })}
      title={`Show traces where ${metadataFilterLabel(key, value)}`}
      className={cn(
        CHIP,
        // 24 px target (WCAG 2.5.8), like the value links.
        "min-h-6 transition-colors hover:bg-surface-selected hover:[&>span:last-child]:underline",
      )}
    >
      <ChipBody pair={pair} />
    </Link>
  );
}
