import { CircleAlert, CircleCheck } from "lucide-react";
import { useId, useRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { markerRanges, markersLeftText, parseRequestBody, type MarkerRange } from "./replay-state";

interface ReplayBodyEditorProps {
  text: string;
  onChange: (text: string) => void;
}

/** Both layers share every metric that affects wrapping, so the marks sit under their text. */
const LAYER =
  "border-[1.5px] px-4 py-3 font-mono text-xs leading-5 whitespace-pre-wrap [overflow-wrap:anywhere] [scrollbar-gutter:stable]";

/** The text with each marker wrapped in a highlight; the text itself stays transparent here. */
function highlighted(text: string, ranges: readonly MarkerRange[]): ReactNode[] {
  const parts: ReactNode[] = [];
  let at = 0;
  for (const range of ranges) {
    parts.push(text.slice(at, range.start));
    parts.push(
      <mark
        key={range.start}
        className="rounded-sm bg-danger-subtle text-transparent underline decoration-danger decoration-2 underline-offset-4"
      >
        {text.slice(range.start, range.end)}
      </mark>,
    );
    at = range.end;
  }
  // The trailing space keeps a final empty line as tall as the textarea's.
  parts.push(`${text.slice(at)} `);
  return parts;
}

function StatusLine({ ok, children }: { ok: boolean; children: ReactNode }) {
  const Icon = ok ? CircleCheck : CircleAlert;
  return (
    <p
      className={cn(
        "flex items-start gap-1.5 text-xs leading-5",
        ok ? "text-success" : "text-danger-text",
      )}
    >
      <Icon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <span className="min-w-0">{children}</span>
    </p>
  );
}

/**
 * Figma "Playground / Replay column (editing)": the request as editable JSON. `[REDACTED]` and
 * `[MAX_DEPTH]` are highlighted behind the text (a mirror layer kept in scroll with the
 * textarea) and listed by line below it, so the state never rests on colour alone.
 */
export function ReplayBodyEditor({ text, onChange }: ReplayBodyEditorProps) {
  const mirror = useRef<HTMLDivElement>(null);
  const statusId = useId();
  const ranges = markerRanges(text);
  const valid = parseRequestBody(text) !== null;
  const markersLeft = markersLeftText(text);

  return (
    <div className="flex flex-col gap-2">
      <div className="relative rounded-input bg-surface">
        <div
          ref={mirror}
          aria-hidden
          className={cn(
            LAYER,
            "pointer-events-none absolute inset-0 overflow-hidden rounded-input border-transparent text-transparent",
          )}
        >
          {highlighted(text, ranges)}
        </div>
        <textarea
          aria-label="Request body (JSON)"
          aria-describedby={statusId}
          aria-invalid={!valid || markersLeft !== null}
          value={text}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          onChange={(event) => onChange(event.target.value)}
          onScroll={(event) => {
            if (mirror.current) {
              mirror.current.scrollTop = event.currentTarget.scrollTop;
              mirror.current.scrollLeft = event.currentTarget.scrollLeft;
            }
          }}
          className={cn(
            LAYER,
            "relative block h-80 w-full resize-y overflow-auto rounded-input border-border bg-transparent text-foreground outline-none focus-visible:border-accent",
          )}
        />
      </div>
      <div id={statusId} aria-live="polite" className="flex flex-col gap-1">
        {valid ? (
          <StatusLine ok>Valid JSON</StatusLine>
        ) : (
          <StatusLine ok={false}>Not valid JSON, or not a JSON object.</StatusLine>
        )}
        {markersLeft ? <StatusLine ok={false}>{markersLeft}</StatusLine> : null}
      </div>
    </div>
  );
}
