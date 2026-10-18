import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";

import { ErrorState } from "@/components/error-state";
import { UnknownValue } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { useProjectParams } from "@/features/shell/project-context";
import type { DetectorRun, Health, Insight, Kpis } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  APPROXIMATE_LINE,
  BAND_LABELS,
  FORMULA_LINE,
  NO_SCORE_REASON,
  componentRows,
  formatPoints,
  healthBand,
  lastPassSummary,
  meterTone,
  ringFraction,
  type ComponentRow,
  type HealthBand,
  type StatusLine,
  type StatusTone,
} from "./doctor-format";
import type { Loadable } from "./spend-card";

interface DoctorCardProps {
  health: Health;
  /** The window's KPIs: the errors row counts failed calls from them. */
  metrics: Kpis;
  /** The newest detector runs, newest first. */
  runs: Loadable<DetectorRun[]>;
  /** The most recently seen open or acknowledged finding, when there are open findings. */
  topInsight: Insight | undefined;
  /** The providers' open-incident line; omitted (null) when its data failed to load. */
  providers: Loadable<StatusLine> | null;
  now: Date;
  className?: string;
}

const BAND_TEXT: Record<HealthBand, string> = {
  good: "text-success",
  fair: "text-warning",
  poor: "text-danger",
  none: "text-subtle-foreground",
};

const BAND_BADGE = {
  good: "success",
  fair: "warning",
  poor: "danger",
  none: "neutral",
} as const satisfies Record<HealthBand, "success" | "warning" | "danger" | "neutral">;

const TONE_DOT: Record<StatusTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  none: "bg-border-strong",
};

const CARD_CLASSES = "flex min-w-0 flex-col justify-between gap-5 p-5 sm:p-6";

/**
 * Figma "Doctor card": the Doctor's 0 to 100 score as a ring, the four score components with
 * their observed value, previous baseline and points off their cap, the top open finding, the
 * last detector pass and the providers' open incidents.
 */
export function DoctorCard({
  health,
  metrics,
  runs,
  topInsight,
  providers,
  now,
  className,
}: DoctorCardProps) {
  const band = healthBand(health.value);
  // Only a known empty list means "no pass yet"; while the runs load the findings stay as reported.
  const hasPass = runs.status !== "success" || runs.data.length > 0;
  const open = health.open_critical + health.open_warning;

  return (
    <Card className={cn(CARD_CLASSES, className)}>
      <div className="flex flex-col gap-5">
        <DoctorHeader band={band} />
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:gap-6">
          <ScoreRing health={health} band={band} />
          <dl className="flex min-w-0 flex-1 flex-col divide-y divide-border">
            {componentRows(health, metrics, hasPass).map((row) => (
              <ComponentRowItem key={row.name} row={row} />
            ))}
          </dl>
        </div>
        {open > 0 && topInsight ? <TopFinding insight={topInsight} now={now} /> : null}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 border-t border-border pt-4 text-xs font-medium text-muted-foreground">
        {runs.status === "pending" ? <Skeleton className="h-4 w-60 max-w-full" /> : null}
        {runs.status === "success" ? <StatusItem line={lastPassSummary(runs.data, now)} /> : null}
        {providers?.status === "pending" ? <Skeleton className="h-4 w-44" /> : null}
        {providers?.status === "success" ? <StatusItem line={providers.data} /> : null}
      </div>
    </Card>
  );
}

function DoctorHeader({ band }: { band: HealthBand }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3">
      <div className="flex items-center gap-2">
        <CardTitle>Doctor</CardTitle>
        <Badge variant={BAND_BADGE[band]} size="sm">
          {BAND_LABELS[band]}
        </Badge>
      </div>
      <Link
        to="/$orgId/$projectId/doctor"
        params={{ orgId, projectId }}
        className="inline-flex min-h-11 w-fit items-center gap-1.5 rounded-sm text-sm font-medium text-accent hover:underline"
      >
        Open the Doctor
        <ArrowRight aria-hidden className="size-3.5" />
      </Link>
    </div>
  );
}

/** label · value and note · penalty meter · points; two lines on phones. */
function ComponentRowItem({ row }: { row: ComponentRow }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 py-[11px] sm:grid-cols-[84px_minmax(0,1fr)_92px_64px]">
      <dt className="text-label font-semibold text-foreground">{row.label}</dt>
      <dd className="text-label text-muted-foreground max-sm:col-start-1 max-sm:row-start-2">
        {row.value ?? <UnknownValue reason={NO_SCORE_REASON} />}
        {row.note ? ` · ${row.note}` : null}
      </dd>
      <dd aria-hidden className="max-sm:col-start-2 max-sm:row-start-2">
        <PenaltyMeter penalty={row.penalty} cap={row.cap} />
      </dd>
      <dd
        className={cn(
          "text-right text-xs font-semibold tabular max-sm:col-start-2 max-sm:row-start-1",
          row.penalty ? "text-foreground" : "text-subtle-foreground",
        )}
      >
        {row.penalty === null ? (
          <UnknownValue reason="Nothing to score for this component yet" />
        ) : (
          formatPoints(row.penalty, row.cap)
        )}
      </dd>
    </div>
  );
}

const METER_FILL = { warning: "bg-warning", danger: "bg-danger" } as const;

function PenaltyMeter({ penalty, cap }: { penalty: number | null; cap: number }) {
  return (
    <div className="h-1.5 w-[92px] overflow-hidden rounded-[3px] bg-surface-muted">
      {penalty ? (
        <div
          className={cn("h-full rounded-[3px]", METER_FILL[meterTone(penalty, cap)])}
          style={{ width: `${Math.min(1, penalty / cap) * 100}%` }}
        />
      ) : null}
    </div>
  );
}

function TopFinding({ insight, now }: { insight: Insight; now: Date }) {
  const { orgId, projectId } = useProjectParams();
  const critical = insight.severity === "critical";
  return (
    <Link
      to="/$orgId/$projectId/doctor/$insightId"
      params={{ orgId, projectId, insightId: insight.id }}
      className={cn(
        "flex min-h-11 items-center gap-2.5 rounded-[14px] px-3.5 py-2.5 hover:underline",
        critical ? "bg-danger-subtle text-danger-text" : "bg-warning-subtle text-warning",
      )}
    >
      <span
        aria-hidden
        className={cn("size-2 shrink-0 rounded-full", critical ? "bg-danger" : "bg-warning")}
      />
      <span className="sr-only">{critical ? "Critical: " : "Warning: "}</span>
      <span className="min-w-0 flex-1 truncate text-label font-semibold">{insight.title}</span>
      <span className="shrink-0 text-xs font-medium">
        {formatRelativeTime(insight.last_seen_at, now)}
        <ArrowRight aria-hidden className="ml-1 inline size-3" />
      </span>
    </Link>
  );
}

function StatusItem({ line }: { line: StatusLine }) {
  return (
    <p className="flex items-center gap-2">
      <span aria-hidden className={cn("size-2 shrink-0 rounded-full", TONE_DOT[line.tone])} />
      {line.text}
    </p>
  );
}

function ScoreRing({ health, band }: { health: Health; band: HealthBand }) {
  const { value } = health;
  const ring = (
    <span className="relative block size-24 shrink-0">
      <svg viewBox="0 0 88 88" aria-hidden className="size-full -rotate-90">
        <circle
          cx="44"
          cy="44"
          r="38"
          fill="none"
          strokeWidth="8"
          className="stroke-surface-muted"
        />
        {value === null ? null : (
          <circle
            cx="44"
            cy="44"
            r="38"
            fill="none"
            strokeWidth="8"
            strokeLinecap="round"
            pathLength={100}
            strokeDasharray={`${ringFraction(value) * 100} 100`}
            className={cn("stroke-current", BAND_TEXT[band])}
          />
        )}
      </svg>
      <span className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        {value === null ? (
          <span className="text-metric-xl text-foreground">
            <UnknownValue reason={NO_SCORE_REASON} />
          </span>
        ) : (
          <>
            <span className="text-h1 text-foreground tabular">{value}</span>
            <span className="mt-0.5 text-xs font-medium text-muted-foreground">/100</span>
          </>
        )}
      </span>
    </span>
  );
  if (value === null) {
    return ring;
  }
  return (
    <Tooltip
      side="bottom"
      content={
        <p className="w-56 font-normal">
          {FORMULA_LINE}
          {health.approximate ? `. ${APPROXIMATE_LINE}` : null}
        </p>
      }
    >
      <button
        type="button"
        aria-label={`Health score ${health.approximate ? "about " : ""}${value} out of 100. How it is calculated`}
        className="flex w-fit shrink-0 cursor-help rounded-full"
      >
        {ring}
      </button>
    </Tooltip>
  );
}

/** Loading placeholder in the card's shape (Figma state "Loading"). */
export function DoctorCardSkeleton({ className }: { className?: string }) {
  return (
    <Card aria-hidden className={cn(CARD_CLASSES, className)}>
      <div className="flex flex-col gap-5">
        <Skeleton className="h-5 w-28" />
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:gap-6">
          <Skeleton className="size-24 shrink-0 rounded-full" />
          <div className="flex flex-1 flex-col gap-5">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-4 w-full" />
            ))}
          </div>
        </div>
      </div>
      <Skeleton className="h-4 w-64 max-w-full" />
    </Card>
  );
}

export function DoctorCardError({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry: () => void;
  className?: string;
}) {
  return (
    <Card className={cn("flex flex-col justify-center", className)}>
      <ErrorState compact error={error} title="Couldn't load the Doctor" onRetry={onRetry} />
    </Card>
  );
}
