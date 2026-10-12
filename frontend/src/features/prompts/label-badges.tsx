import { Tag } from "lucide-react";

import type { PromptKind, PromptLabel } from "@/lib/api";
import { cn } from "@/lib/utils";

const CHIP = "inline-flex shrink-0 items-center gap-[5px] rounded-full px-2.5 py-[3px] text-xs";

/** Figma "Prompts/Kind chip": `text` or `chat`, outlined and muted. */
export function KindChip({ kind, className }: { kind: PromptKind; className?: string }) {
  return (
    <span
      className={cn(
        CHIP,
        "border border-border-strong bg-surface font-medium text-muted-foreground",
        className,
      )}
    >
      {kind}
    </span>
  );
}

interface LabelBadgeProps {
  label: string;
  /** Shown after the label ("production · v2") where the row doesn't already say the version. */
  version?: number | undefined;
}

/**
 * Figma "Prompts/Label badge". `production` is violet; every other label (staging and custom
 * ones) is neutral with a strong outline, so no label borrows the warning tone.
 */
export function LabelBadge({ label, version }: LabelBadgeProps) {
  const production = label === "production";
  return (
    <span
      className={cn(
        CHIP,
        "font-semibold",
        production
          ? "bg-accent-subtle text-accent"
          : "border border-border-strong bg-surface-muted text-foreground",
      )}
    >
      <Tag aria-hidden className="size-3" strokeWidth={2.25} />
      {version === undefined ? label : `${label} · v${String(version)}`}
    </span>
  );
}

/** The implicit `latest` label: always the newest version, so it's quieter than real labels. */
export function LatestBadge() {
  return (
    <span
      className={cn(
        CHIP,
        "border border-border bg-surface-muted font-semibold text-muted-foreground",
      )}
    >
      <Tag aria-hidden className="size-3" strokeWidth={2.25} />
      latest
    </span>
  );
}

interface LabelBadgesProps {
  labels: readonly PromptLabel[];
  showVersion?: boolean;
  className?: string;
}

/** A prompt's labels, `production` first, or "No labels". */
export function LabelBadges({ labels, showVersion = false, className }: LabelBadgesProps) {
  if (labels.length === 0) {
    return <span className="text-xs text-subtle-foreground">No labels</span>;
  }
  const ordered = [...labels].sort((a, b) => {
    if (a.label === "production" || b.label === "production") {
      return a.label === "production" ? -1 : 1;
    }
    return a.label.localeCompare(b.label);
  });
  return (
    <span className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {ordered.map((label) => (
        <LabelBadge
          key={label.label}
          label={label.label}
          version={showVersion ? label.version : undefined}
        />
      ))}
    </span>
  );
}
