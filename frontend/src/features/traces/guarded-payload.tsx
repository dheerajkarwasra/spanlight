import { Download, Info } from "lucide-react";
import { useMemo, useState } from "react";

import { JsonViewer } from "@/components/json-viewer";
import { ShowAllButton } from "@/components/show-all-button";
import { Button } from "@/components/ui/button";
import { charactersLabel, TEXT_CLAMP_CHARS } from "@/lib/clamp";

import { formatPayloadBytes, guardPayload, PREVIEW_BYTES } from "./payload-guard";

interface GuardedPayloadProps {
  label: string;
  /** A text (string) or JSON payload. */
  value: unknown;
  /** `value` as Copy and Download give it: strings verbatim, the rest as JSON. */
  serialized: string;
}

/**
 * Figma "D6": a text or JSON payload over 64 KB shows its first 16 KB as text until "Load all",
 * so no huge text node lands in the page unasked. Chat bubbles clamp each text on their own.
 */
export function GuardedPayload({ label, value, serialized }: GuardedPayloadProps) {
  const [loadAll, setLoadAll] = useState(false);
  const guard = useMemo(() => guardPayload(serialized), [serialized]);

  if (!guard.truncated || loadAll) {
    return typeof value === "string" ? (
      <div
        role="region"
        aria-label={`${label} text`}
        tabIndex={0}
        className="max-h-[32rem] overflow-auto rounded-input border border-border bg-surface-muted px-3 py-3.5"
      >
        <ClampedPre text={value} initiallyExpanded={loadAll} />
      </div>
    ) : (
      <JsonViewer value={value} className="rounded-input px-3 py-3.5" />
    );
  }

  const download = () => {
    const isText = typeof value === "string";
    const url = URL.createObjectURL(
      new Blob([serialized], { type: isText ? "text/plain;charset=utf-8" : "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${label.toLowerCase()}.${isText ? "txt" : "json"}`;
    link.click();
    // Safari reads the object URL after the click returns, so let go of it a little later.
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
    }, 1000);
  };

  return (
    <div className="flex flex-col gap-2 rounded-input border border-border bg-surface-muted px-3 py-3.5">
      <div
        role="region"
        aria-label={`${label} preview`}
        tabIndex={0}
        className="max-h-[32rem] overflow-auto"
      >
        <pre className="text-label leading-normal break-words whitespace-pre-wrap text-foreground">
          {guard.shown}
        </pre>
      </div>
      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
        <Info aria-hidden className="size-3.5 shrink-0" />
        <span>{`Showing ${formatPayloadBytes(PREVIEW_BYTES)} of ${formatPayloadBytes(guard.totalBytes)}`}</span>
        <Separator />
        <Button
          variant="link"
          size="sm"
          onClick={() => {
            setLoadAll(true);
          }}
          className="min-h-6"
        >
          Load all
        </Button>
        <Separator />
        <Button variant="link" size="sm" onClick={download} className="min-h-6">
          <Download aria-hidden />
          Download
        </Button>
      </p>
    </div>
  );
}

function Separator() {
  return (
    <span aria-hidden className="text-subtle-foreground">
      {" · "}
    </span>
  );
}

/** Plain text, its first `TEXT_CLAMP_CHARS` until "Show all", so a 1 MiB text stays responsive. */
function ClampedPre({ text, initiallyExpanded }: { text: string; initiallyExpanded: boolean }) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const isLong = text.length > TEXT_CLAMP_CHARS;
  return (
    <>
      <pre className="text-label leading-normal break-words whitespace-pre-wrap text-foreground">
        {isLong && !expanded ? `${text.slice(0, TEXT_CLAMP_CHARS)}…` : text}
      </pre>
      {isLong ? (
        <ShowAllButton
          expanded={expanded}
          onToggle={() => {
            setExpanded((current) => !current);
          }}
          label={charactersLabel(text.length)}
          className="mt-2"
        />
      ) : null}
    </>
  );
}
