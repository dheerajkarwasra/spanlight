import type { ReactNode } from "react";

import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** `on-ink` is for the dark header bands (trace, user): light-on-dark placeholder colours. */
export type UnknownTone = "default" | "on-ink";

const TONE_CLASSES: Record<UnknownTone, string> = {
  default: "text-subtle-foreground decoration-border",
  "on-ink": "text-rail-muted-foreground decoration-rail-subtle-foreground",
};

const DASH_UNDERLINE = "underline decoration-dotted underline-offset-4";

/**
 * The dash's look (default tone), for an unknown value shown inside a control that already
 * carries the explanation, where a nested `UnknownValue` trigger would be invalid.
 */
export const UNKNOWN_DASH_CLASS = `${DASH_UNDERLINE} ${TONE_CLASSES.default}`;

interface UnknownValueProps {
  /**
   * Why the value is missing, e.g. "No price for this model". A reason that already leads with
   * its own clause ("Unknown: …", "Not measured: …") is read out as written.
   */
  reason: string;
  tone?: UnknownTone;
  className?: string;
}

/** "Unknown: …", "Not measured: …": a short capitalised clause and a colon. */
const LEADING_CLAUSE = /^[A-Z][a-z]*(?: [a-z]+){0,2}:/;

/** The shared placeholder for unknown metrics: an em dash with an explanation. */
export function UnknownValue({ reason, tone = "default", className }: UnknownValueProps) {
  return (
    <Tooltip content={reason}>
      <span
        tabIndex={0}
        aria-label={LEADING_CLAUSE.test(reason) ? reason : `Unknown: ${reason}`}
        className={cn("cursor-help rounded-sm", DASH_UNDERLINE, TONE_CLASSES[tone], className)}
      >
        —
      </span>
    </Tooltip>
  );
}

interface ValueOrUnknownProps {
  value: ReactNode | null;
  reason: string;
  className?: string;
}

/** Renders `value`, or the unknown placeholder when the value is null. */
export function ValueOrUnknown({ value, reason, className }: ValueOrUnknownProps) {
  if (value === null || value === undefined || value === "") {
    return <UnknownValue reason={reason} className={className} />;
  }
  return <span className={className}>{value}</span>;
}
