import { Badge } from "@/components/ui/badge";
import type { DiffLine } from "@/lib/text-diff";
import { cn } from "@/lib/utils";

import type { ConfigChange, MessageDiff, MessageStatus } from "./diff";
import { HighlightedText } from "./highlighted-text";

const LINE_TONES: Record<DiffLine["kind"], string> = {
  same: "",
  added: "bg-success-subtle",
  removed: "bg-danger-subtle",
};

/** Lines of a diff: old and new line numbers, a +/− mark (also read out) and the text. */
export function DiffLines({ lines, label }: { lines: readonly DiffLine[]; label: string }) {
  return (
    <ol className="flex flex-col gap-0.5 font-mono" aria-label={label}>
      {lines.map((line, index) => (
        <li
          key={index}
          className={cn("flex items-start gap-1.5 rounded-lg px-1.5 py-2.5", LINE_TONES[line.kind])}
        >
          <span aria-hidden className="w-6 shrink-0 text-xs text-subtle-foreground tabular">
            {line.oldNumber}
          </span>
          <span aria-hidden className="w-6 shrink-0 text-xs text-subtle-foreground tabular">
            {line.newNumber}
          </span>
          <span
            aria-hidden
            className={cn(
              "w-3.5 shrink-0 text-[13px]",
              line.kind === "added" && "text-success",
              line.kind === "removed" && "text-danger-text",
            )}
          >
            {line.kind === "added" ? "+" : line.kind === "removed" ? "−" : ""}
          </span>
          <span className="min-w-0 flex-1 text-[13px] leading-5 [overflow-wrap:anywhere] whitespace-pre-wrap text-foreground">
            {line.kind === "same" ? null : (
              <span className="sr-only">{line.kind === "added" ? "Added: " : "Removed: "}</span>
            )}
            <HighlightedText text={line.text} />
          </span>
        </li>
      ))}
    </ol>
  );
}

const STATUS: Record<
  MessageStatus,
  { label: string; variant: "warning" | "outline" | "success" | "danger" }
> = {
  edited: { label: "Edited", variant: "warning" },
  unchanged: { label: "Unchanged", variant: "outline" },
  added: { label: "Added", variant: "success" },
  removed: { label: "Removed", variant: "danger" },
};

/** One chat message of the diff. An unchanged message is collapsed to its header. */
export function MessageDiffBlock({ message }: { message: MessageDiff }) {
  const status = STATUS[message.status];
  const role = message.previousRole ? `${message.previousRole} → ${message.role}` : message.role;
  return (
    <div
      role="group"
      aria-label={`Message ${String(message.index + 1)}, ${role}, ${status.label.toLowerCase()}`}
      className="flex flex-col gap-2.5 rounded-tile bg-surface-muted p-3 sm:p-4"
    >
      <div className="flex items-center gap-2.5">
        <span className="text-overline font-semibold text-subtle-foreground uppercase">{role}</span>
        <Badge variant={status.variant} size="sm">
          {status.label}
        </Badge>
      </div>
      {message.status === "unchanged" ? null : (
        <DiffLines lines={message.lines} label={`Lines of message ${String(message.index + 1)}`} />
      )}
    </div>
  );
}

const CONFIG_LABELS: Record<ConfigChange["key"], string> = {
  model: "model",
  temperature: "temperature",
  max_tokens: "max tokens",
  top_p: "top p",
};

/** "MODEL CONFIG temperature 0.4 → 0.2", one row per changed field. */
export function ConfigChanges({ changes }: { changes: readonly ConfigChange[] }) {
  return (
    <ul className="flex flex-col gap-2" aria-label="Model config changes">
      {changes.map((change) => (
        <li
          key={change.key}
          className="flex flex-wrap items-center gap-2.5 rounded-tile bg-surface-muted px-4 py-3"
        >
          <span className="text-overline font-semibold text-subtle-foreground uppercase">
            Model config
          </span>
          <span className="font-mono text-[13px] text-foreground">{CONFIG_LABELS[change.key]}</span>
          <Badge variant="danger">
            <span className="sr-only">was </span>
            {change.before ?? "unset"}
          </Badge>
          <span aria-hidden className="text-[13px] text-muted-foreground">
            →
          </span>
          <Badge variant="success">
            <span className="sr-only">now </span>
            {change.after ?? "unset"}
          </Badge>
        </li>
      ))}
    </ul>
  );
}
