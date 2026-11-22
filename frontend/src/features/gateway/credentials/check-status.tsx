import type { ReactNode } from "react";

import { CircleAlert, CircleCheck, CircleDashed, Loader2 } from "lucide-react";

import { formatRelativeTime, formatTimestamp } from "@/lib/format";
import type { Credential } from "@/lib/api";
import { cn } from "@/lib/utils";

import { checkAdvice, checkState } from "./credential-model";

interface CheckStatusProps {
  credential: Credential;
  /** A check request is running for this credential. */
  checking: boolean;
  className?: string;
}

/**
 * Figma "Gateway/Check status chip": working, failing, never checked, and "Checking…" while a
 * check runs. Colour never carries the state alone: every state has an icon and words.
 */
export function CheckStatus({ credential, checking, className }: CheckStatusProps) {
  if (checking) {
    return (
      <Chip tone="neutral" className={className}>
        <Loader2 aria-hidden className="animate-spin" />
        <span>Checking…</span>
      </Chip>
    );
  }

  const state = checkState(credential);
  switch (state.kind) {
    case "working":
      return (
        <Chip tone="success" className={className}>
          <CircleCheck aria-hidden />
          <span>
            Working ·{" "}
            <time dateTime={state.checkedAt} title={formatTimestamp(state.checkedAt) ?? undefined}>
              checked {formatRelativeTime(state.checkedAt) ?? formatTimestamp(state.checkedAt)}
            </time>
          </span>
        </Chip>
      );
    case "failing": {
      const advice = checkAdvice(credential);
      return (
        <div className={cn("min-w-0 space-y-1", className)}>
          <Chip tone="danger">
            <CircleAlert aria-hidden />
            <span title={state.error} className="truncate">
              Failing · {state.error}
            </span>
          </Chip>
          {advice ? <p className="text-xs text-muted-foreground">{advice}</p> : null}
        </div>
      );
    }
    case "never":
      return (
        <Chip tone="neutral" className={className}>
          <CircleDashed aria-hidden />
          <span>Never checked</span>
        </Chip>
      );
  }
}

const TONES = {
  success: "bg-success-subtle text-success",
  danger: "bg-danger-subtle text-danger-text",
  neutral: "bg-surface text-muted-foreground",
} as const;

function Chip({
  tone,
  className,
  children,
}: {
  tone: keyof typeof TONES;
  className?: string | undefined;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold [&_svg]:size-3.5 [&_svg]:shrink-0",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
