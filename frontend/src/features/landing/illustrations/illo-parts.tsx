import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The shared vocabulary of the drawn landing illustrations (Figma "Landing v4 — Illustrations"):
 * a small header, rows with a coloured dot and a mono label, status pills and a note bubble.
 * Every illustration is decoration inside a tile whose text carries the meaning.
 */

const DOT_CLASS = {
  llm: "bg-kind-llm",
  chain: "bg-kind-chain",
  tool: "bg-kind-tool",
  retrieval: "bg-kind-retrieval",
  warning: "bg-warning",
  danger: "bg-danger",
  cost: "bg-chart-4",
  accent: "bg-accent",
  lime: "bg-lime",
  none: "bg-border",
} as const;

export type DotTone = keyof typeof DOT_CLASS;

const PILL = {
  muted: "bg-surface-muted text-muted-foreground",
  lime: "bg-lime text-lime-foreground",
  accent: "bg-accent-subtle text-accent-subtle-foreground",
  success: "bg-success-subtle text-foreground",
  warning: "bg-warning-subtle text-foreground",
  danger: "bg-danger-subtle text-danger-text",
  rail: "bg-rail-tile text-rail-foreground",
  ink: "bg-hero-card text-hero-card-foreground dark:border dark:border-border",
} as const;

export type PillTone = keyof typeof PILL;

export function Pill({
  tone = "muted",
  mono = false,
  className,
  children,
}: {
  tone?: PillTone;
  mono?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "shrink-0 rounded-full px-2.5 py-1 text-xs leading-[1.4] font-medium whitespace-nowrap",
        mono && "font-mono font-normal",
        PILL[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Dot({ tone }: { tone: DotTone }) {
  return <span className={cn("size-2 shrink-0 rounded-full", DOT_CLASS[tone])} />;
}

/** A waterfall bar in a span's colour; start and length are percentages of the track. */
export function SpanBar({ tone, start, length }: { tone: DotTone; start: number; length: number }) {
  return (
    <span
      className={cn("absolute inset-y-0 min-w-1.5 rounded-full", DOT_CLASS[tone])}
      style={{ left: `${start}%`, width: `${length}%` }}
    />
  );
}

/** Small uppercase label on the left, an optional pill on the right. */
export function IlloHeader({
  label,
  onInk = false,
  children,
}: {
  label: ReactNode;
  onInk?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 pb-1">
      <span
        className={cn(
          "truncate text-overline uppercase",
          onInk ? "text-rail-muted-foreground" : "text-subtle-foreground",
        )}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

const ROW = {
  muted: "bg-surface-muted text-foreground",
  outline: "border border-dashed border-border bg-surface text-foreground",
  rail: "bg-rail-tile text-rail-foreground",
  ink: "bg-hero-card text-hero-card-foreground dark:border dark:border-border",
} as const;

/** One row: dot, label, then whatever sits on the right (pills, an amount). */
export function IlloRow({
  label,
  dot,
  tone = "muted",
  mono = true,
  children,
}: {
  label: ReactNode;
  dot?: DotTone;
  tone?: keyof typeof ROW;
  mono?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className={cn("flex items-center gap-2.5 rounded-input px-3.5 py-3", ROW[tone])}>
      {dot ? <Dot tone={dot} /> : null}
      <span
        className={cn(
          "min-w-0 flex-1 truncate",
          mono ? "font-mono text-label" : "text-sm font-medium",
        )}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

/** A dark note bubble, aligned right. */
export function IlloNote({ onInk = false, children }: { onInk?: boolean; children: ReactNode }) {
  return (
    <div className="flex justify-end">
      <span
        className={cn(
          "rounded-md px-[13px] py-[9px] text-xs font-medium",
          onInk
            ? "bg-rail-tile text-rail-foreground"
            : "bg-hero-card text-hero-card-foreground dark:border dark:border-border",
        )}
      >
        {children}
      </span>
    </div>
  );
}
