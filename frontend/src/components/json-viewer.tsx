import { Braces, Check, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";

import { ShowAllButton } from "@/components/show-all-button";
import { Button } from "@/components/ui/button";
import { charactersLabel, TEXT_CLAMP_CHARS } from "@/lib/clamp";
import { cn } from "@/lib/utils";

/** The most entries an open object or array renders before "Show all", for large payloads. */
const ENTRY_CLAMP = 200;

/** A string longer than this, or with a newline, reads as prose rather than a JSON literal. */
const PROSE_CHARS = 200;

function isProse(value: unknown): value is string {
  return typeof value === "string" && (value.length > PROSE_CHARS || value.includes("\n"));
}

function containsProse(value: unknown): boolean {
  if (isProse(value)) {
    return true;
  }
  return value !== null && typeof value === "object" && Object.values(value).some(containsProse);
}

interface JsonViewerProps {
  value: unknown;
  /** Nodes deeper than this start collapsed. */
  defaultExpandDepth?: number;
  /** Long and multi-line strings read as prose (with a "View as JSON" toggle); off keeps raw. */
  prose?: boolean;
  className?: string;
}

/**
 * A read-only, collapsible JSON tree. Keys and values are selectable text. Long or multi-line
 * strings (prompts, documents) read as wrapped prose; "View as JSON" shows them as literals.
 */
export function JsonViewer({
  value,
  defaultExpandDepth = 2,
  prose = true,
  className,
}: JsonViewerProps) {
  const hasProse = useMemo(() => prose && containsProse(value), [prose, value]);
  const [asJson, setAsJson] = useState(false);
  return (
    <div
      className={cn(
        "overflow-x-auto rounded-tile border border-border bg-surface-muted p-4 font-mono text-label leading-6",
        className,
      )}
    >
      {hasProse ? (
        <div className="mb-1 flex justify-end font-sans">
          <Button
            size="sm"
            aria-pressed={asJson}
            onClick={() => {
              setAsJson((current) => !current);
            }}
            className={cn("h-7 px-3", asJson && "border-accent text-accent")}
          >
            {asJson ? <Check aria-hidden /> : <Braces aria-hidden />}
            View as JSON
          </Button>
        </div>
      ) : null}
      <JsonNode
        value={value}
        depth={0}
        defaultExpandDepth={defaultExpandDepth}
        prose={hasProse && !asJson}
      />
    </div>
  );
}

interface JsonNodeProps {
  name?: string;
  value: unknown;
  depth: number;
  defaultExpandDepth: number;
  isLast?: boolean;
  /** Long and multi-line strings render as prose. */
  prose: boolean;
}

function JsonNode({ name, value, depth, defaultExpandDepth, isLast = true, prose }: JsonNodeProps) {
  const [expanded, setExpanded] = useState(depth < defaultExpandDepth);
  const [allEntries, setAllEntries] = useState(false);
  const comma = isLast ? null : <span className="text-subtle-foreground">,</span>;
  const label =
    name === undefined ? null : (
      <>
        <span className="text-accent-subtle-foreground">{JSON.stringify(name)}</span>
        <span className="text-subtle-foreground">: </span>
      </>
    );

  if (prose && isProse(value)) {
    return (
      <div className="pl-4">
        {label}
        <ProseString value={value} />
        {comma}
      </div>
    );
  }

  if (value === null || typeof value !== "object") {
    return (
      <div className="pl-4 break-all whitespace-pre-wrap">
        {label}
        <JsonPrimitive value={value} />
        {comma}
      </div>
    );
  }

  const isArray = Array.isArray(value);
  const entries: [string, unknown][] = isArray
    ? value.map((item: unknown, index) => [String(index), item])
    : Object.entries(value);
  const [open, close] = isArray ? ["[", "]"] : ["{", "}"];

  if (entries.length === 0) {
    return (
      <div className="pl-4">
        {label}
        <span className="text-subtle-foreground">
          {open}
          {close}
        </span>
        {comma}
      </div>
    );
  }

  const summary = isArray ? `${entries.length} items` : `${entries.length} keys`;
  const clamped = entries.length > ENTRY_CLAMP;
  const shown = clamped && !allEntries ? entries.slice(0, ENTRY_CLAMP) : entries;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => {
          setExpanded((current) => !current);
        }}
        aria-expanded={expanded}
        className="group inline-flex items-start rounded-xs text-left hover:bg-surface"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "mt-1 size-4 shrink-0 text-subtle-foreground transition-transform",
            expanded && "rotate-90",
          )}
        />
        <span>
          {label}
          <span className="text-subtle-foreground">{open}</span>
          {expanded ? null : (
            <>
              <span className="px-1 text-subtle-foreground italic">{summary}</span>
              <span className="text-subtle-foreground">{close}</span>
              {comma}
            </>
          )}
        </span>
      </button>
      {expanded ? (
        <>
          <div className="ml-2 border-l border-border pl-2">
            {shown.map(([key, child], index) => (
              <JsonNode
                key={key}
                name={isArray ? undefined : key}
                value={child}
                depth={depth + 1}
                defaultExpandDepth={defaultExpandDepth}
                isLast={index === entries.length - 1}
                prose={prose}
              />
            ))}
            {clamped ? (
              <ShowAllButton
                expanded={allEntries}
                onToggle={() => {
                  setAllEntries((current) => !current);
                }}
                label={summary}
                className="ml-4 font-sans"
              />
            ) : null}
          </div>
          <div className="pl-4">
            <span className="text-subtle-foreground">{close}</span>
            {comma}
          </div>
        </>
      ) : null}
    </div>
  );
}

function JsonPrimitive({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-subtle-foreground">null</span>;
  }
  if (typeof value === "string") {
    return value.length > TEXT_CLAMP_CHARS ? (
      <LongString value={value} />
    ) : (
      <span className="text-syntax-string">{JSON.stringify(value)}</span>
    );
  }
  if (typeof value === "number") {
    return <span className="text-kind-chain">{value}</span>;
  }
  if (typeof value === "boolean") {
    return <span className="text-kind-llm">{String(value)}</span>;
  }
  return <span>{JSON.stringify(value)}</span>;
}

/** Figma "D14": sans, wrapped, newlines kept, the quotes as faint marks. */
function ProseString({ value }: { value: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = value.length > TEXT_CLAMP_CHARS;
  return (
    <div className="font-sans">
      <span aria-hidden className="text-subtle-foreground">
        “
      </span>
      <span className="font-sans break-words whitespace-pre-wrap text-foreground">
        {isLong && !expanded ? `${value.slice(0, TEXT_CLAMP_CHARS)}…` : value}
      </span>
      <span aria-hidden className="text-subtle-foreground">
        ”
      </span>
      {isLong ? (
        <ShowAllButton
          expanded={expanded}
          onToggle={() => {
            setExpanded((current) => !current);
          }}
          label={charactersLabel(value.length)}
          className="ml-2"
        />
      ) : null}
    </div>
  );
}

function LongString({ value }: { value: string }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded
    ? JSON.stringify(value)
    : `${JSON.stringify(value.slice(0, TEXT_CLAMP_CHARS))}…`;
  return (
    <>
      <span className="text-syntax-string">{shown}</span>{" "}
      <ShowAllButton
        expanded={expanded}
        onToggle={() => {
          setExpanded((current) => !current);
        }}
        label={charactersLabel(value.length)}
        className="font-sans"
      />
    </>
  );
}
