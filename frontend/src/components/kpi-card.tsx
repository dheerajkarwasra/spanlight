import { Link, type LinkProps } from "@tanstack/react-router";
import { Clock } from "lucide-react";
import { useId, type ReactNode } from "react";

import { DeltaPill, type DeltaPillProps } from "@/components/delta-pill";
import { Sparkline, type SparklineTone } from "@/components/sparkline";
import { Card } from "@/components/ui/card";
import { UNKNOWN_DASH_CLASS, UnknownValue } from "@/components/unknown-value";
import { cn } from "@/lib/utils";

export interface KpiCardProps {
  label: string;
  /** The formatted value; `null` when unknown, shown as "—" with `unknownReason`. */
  value: ReactNode | null;
  unknownReason?: string;
  unit?: string;
  delta?: DeltaPillProps;
  /** e.g. "15 of 432 calls"; a node when part of it needs a tone (the Doctor's critical count). */
  denominator?: ReactNode;
  /** "approx" puts the "≈" before the value; `freshness` is a short "updated …" line. */
  chip?: "approx" | { freshness: string };
  sparkline?: { values: (number | null)[]; tone: SparklineTone };
  /** Makes the whole card one link, named by its label and value. */
  href?: LinkProps;
  tone?: "default" | "accent";
  children?: ReactNode;
}

const DEFAULT_REASON = "Not known for this window";

/** A headline number (spec §3.3): label, value, delta, a muted line under it and a sparkline. */
export function KpiCard({
  label,
  value,
  unknownReason = DEFAULT_REASON,
  unit,
  delta,
  denominator,
  chip,
  sparkline,
  href,
  tone = "default",
  children,
}: KpiCardProps) {
  const id = useId();
  const accent = tone === "accent";
  const muted = accent ? "" : "text-muted-foreground";

  const card = (
    <Card
      variant={accent ? "accent" : "default"}
      className={cn(
        "flex h-full min-h-[110px] min-w-0 flex-col gap-1 p-4 transition-colors",
        href && !accent && "hover:bg-surface-hover",
      )}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        <p id={`${id}-label`} className={cn("truncate text-xs font-medium", muted)}>
          {label}
          <span className="sr-only">:</span>
        </p>
        {delta ? (
          <span id={`${id}-delta`}>
            <DeltaPill surface={accent ? "accent" : "default"} {...delta} />
          </span>
        ) : null}
      </div>
      <p
        id={`${id}-value`}
        className={cn(
          "text-metric [font-size:1.75rem] whitespace-nowrap",
          !accent && "text-foreground",
        )}
      >
        {value === null ? (
          href ? (
            // A focusable tooltip trigger can't sit inside the card's link; the reason is read instead.
            <span title={unknownReason}>
              <span aria-hidden className={UNKNOWN_DASH_CLASS}>
                —
              </span>
              <span className="sr-only">Unknown: {unknownReason}</span>
            </span>
          ) : (
            <UnknownValue reason={unknownReason} />
          )
        ) : (
          <>
            {chip === "approx" ? (
              <>
                <span aria-hidden className="mr-px text-subtle-foreground">
                  ≈
                </span>
                <span className="sr-only">approximately </span>
              </>
            ) : null}
            {value}
            {unit ? <span className={cn("ml-1 text-sm font-semibold", muted)}>{unit}</span> : null}
          </>
        )}
      </p>
      {denominator ? (
        <p id={`${id}-denominator`} className={cn("truncate text-xs tabular", muted)}>
          {denominator}
        </p>
      ) : null}
      {typeof chip === "object" ? (
        <p className={cn("flex items-center gap-1 text-2xs", muted)}>
          <Clock aria-hidden className="size-3" />
          {chip.freshness}
        </p>
      ) : null}
      {children}
      {sparkline ? (
        <div className="mt-auto pt-1">
          <Sparkline values={sparkline.values} tone={sparkline.tone} className="h-9" />
        </div>
      ) : null}
    </Card>
  );

  if (!href) {
    return card;
  }
  return (
    <Link
      {...href}
      aria-labelledby={`${id}-label ${id}-value`}
      aria-describedby={
        [denominator ? `${id}-denominator` : null, delta ? `${id}-delta` : null]
          .filter(Boolean)
          .join(" ") || undefined
      }
      className="block h-full rounded-card focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      {card}
    </Link>
  );
}
