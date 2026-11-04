import { Link } from "@tanstack/react-router";
import { Check, Clock, DollarSign, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { CostValue, UNKNOWN_TOTAL_REASON } from "@/components/cost-value";
import { ErrorState } from "@/components/error-state";
import { StatusDot } from "@/components/status-dot";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { TraceSummary } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ONBOARDING_COPY } from "./copy";
import { formatArrival } from "./first-trace";
import { useFirstTrace } from "./use-onboarding-status";

interface WaitingForTraceProps {
  /** The latest polling error, including failures after an earlier successful refresh. */
  error: Error | null;
  onRetry: () => void;
  /** On the "No SDK" tab the next step is a gateway key, not the ingest snippet. */
  gateway?: boolean;
  /** A setup test trace was accepted: say so and drop the "send or run" helper. */
  testSent?: boolean;
}

/** "Waiting for your first trace…" row with a live pulse (Figma "Status — waiting"). */
export function WaitingForTrace({
  error,
  onRetry,
  gateway = false,
  testSent = false,
}: WaitingForTraceProps) {
  if (error) {
    return (
      <Card>
        <ErrorState
          compact
          error={error}
          title="Checking interrupted"
          description="We couldn't refresh. Your key and snippets are still here."
          onRetry={onRetry}
        />
      </Card>
    );
  }

  const [waitingTitle, waitingHelper] = ONBOARDING_COPY.connect.helper ?? [];
  return (
    <Card
      className={cn(
        "items-center py-[18px] pr-6 pl-[18px]",
        // Nothing is polled on the gateway tab, so no pulse.
        gateway ? "flex" : "grid grid-cols-[2.25rem_minmax(0,1fr)] gap-x-4",
      )}
    >
      {gateway ? null : <LiveIndicator />}
      {/* A live region, so "Test trace sent" is announced without moving focus. */}
      <div role="status" className="flex flex-col gap-0.5">
        <p className="text-sm font-semibold text-foreground">
          {gateway
            ? "Create a gateway key to send your first call"
            : testSent
              ? "Test trace received"
              : waitingTitle}
        </p>
        <p className="text-xs font-medium text-muted-foreground">
          {gateway
            ? "Calls show up here once a key exists and a snippet runs."
            : testSent
              ? "Ingestion works. Instrument your app to connect it."
              : waitingHelper}
        </p>
      </div>
    </Card>
  );
}

/** A 36px ink disc with a lime halo and a pulsing lime dot. Decorative. */
function LiveIndicator() {
  return (
    <span
      aria-hidden
      className="relative flex size-9 items-center justify-center rounded-full bg-hero-card"
    >
      <span className="absolute size-[22px] rounded-full bg-lime/30" />
      <StatusDot state="live" className="relative size-2.5" />
    </span>
  );
}

/** Light ring for controls on the violet card, where the violet ring would disappear. */
const ACCENT_FOCUS = "focus-visible:outline-accent-card-foreground";

interface FirstTraceCardProps {
  orgId: string;
  projectId: string;
  firstTraceAt: string | null;
  traceId: string | null;
}

/** The violet success card (Figma "Success card") with the first trace's details. */
export function FirstTraceCard({ orgId, projectId, firstTraceAt, traceId }: FirstTraceCardProps) {
  const arrival = formatArrival(firstTraceAt);
  const trace = useFirstTrace(projectId, traceId);

  return (
    <Card variant="accent" className="flex flex-col gap-5 overflow-hidden p-6 sm:p-8">
      <div className="flex items-center gap-4">
        <span
          aria-hidden
          className="flex size-12 shrink-0 items-center justify-center rounded-full bg-accent-card-foreground/20"
        >
          <Check className="size-6" strokeWidth={2.25} />
        </span>
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="text-h2">Your app is connected</h2>
          <p className="text-sm">
            {trace.data?.cost_usd === null ? (
              "Application trace received. Cost is unavailable for this trace."
            ) : arrival ? (
              <>
                Application trace received{" "}
                <time dateTime={firstTraceAt ?? undefined}>{arrival}</time>.
              </>
            ) : (
              "Application trace received."
            )}
          </p>
        </div>
      </div>

      <TraceChips trace={trace} />

      {isLoading(trace) ? null : (
        <Button
          asChild
          variant="primary"
          size="lg"
          className={cn("w-full sm:w-auto sm:self-start", ACCENT_FOCUS)}
        >
          {trace.data ? (
            <Link
              to="/$orgId/$projectId/traces/$traceId"
              params={{ orgId, projectId, traceId: trace.data.trace_id }}
            >
              Open your first trace
            </Link>
          ) : (
            // The trace couldn't be loaded; the list keeps the page from being a dead end.
            <Link to="/$orgId/$projectId/traces" params={{ orgId, projectId }}>
              View traces
            </Link>
          )}
        </Button>
      )}
    </Card>
  );
}

const CHIP_PLACEHOLDER =
  "h-8 animate-pulse rounded-full bg-hero-card/15 motion-reduce:animate-none";

function isLoading(trace: ReturnType<typeof useFirstTrace>): boolean {
  return trace.isPending && trace.fetchStatus !== "idle";
}

/**
 * Name, duration and cost of the first trace. Chips without a value are left out; while the
 * trace loads, pill-shaped placeholders hold the row.
 */

function TraceChips({ trace }: { trace: ReturnType<typeof useFirstTrace> }) {
  if (isLoading(trace)) {
    return (
      <div aria-hidden className="flex gap-2">
        <span className={cn(CHIP_PLACEHOLDER, "w-28")} />
        <span className={cn(CHIP_PLACEHOLDER, "w-20")} />
        <span className={cn(CHIP_PLACEHOLDER, "w-24")} />
      </div>
    );
  }

  const chips = trace.data ? traceChips(trace.data) : [];
  if (chips.length === 0) {
    return null;
  }

  return (
    <ul aria-label="First trace" className="flex flex-wrap gap-2">
      {chips.map((chip) => (
        <li
          key={chip.label}
          className={cn(
            "flex h-8 max-w-full min-w-0 items-center gap-1.5 rounded-full bg-hero-card/15 pr-3",
            chip.icon ? "pl-2.5" : "pl-3",
          )}
        >
          {chip.icon ? (
            <chip.icon aria-hidden className="size-3.5 shrink-0" strokeWidth={2} />
          ) : null}
          <span className="sr-only">{chip.label}: </span>
          {chip.value}
        </li>
      ))}
    </ul>
  );
}

interface TraceChip {
  label: string;
  icon: LucideIcon | null;
  value: ReactNode;
}

function traceChips(trace: TraceSummary): TraceChip[] {
  const chips: TraceChip[] = [];
  if (trace.name) {
    chips.push({
      label: "Name",
      icon: null,
      value: <code className="min-w-0 truncate text-label">{trace.name}</code>,
    });
  }
  const duration = formatDuration(trace.duration_ms);
  if (duration) {
    chips.push({
      label: "Duration",
      icon: Clock,
      value: <span className="text-sm font-medium tabular">{duration}</span>,
    });
  }
  chips.push({
    label: "Cost",
    icon: DollarSign,
    value: (
      <span className="inline-flex items-center gap-1 text-sm font-medium tabular">
        {trace.cost_usd !== null && trace.has_unpriced ? "≥ " : null}
        <CostValue
          cost={trace.cost_usd}
          hasUnpriced={trace.has_unpriced}
          unknownReason={UNKNOWN_TOTAL_REASON}
          className="text-current"
        />
        {trace.cost_usd !== null && trace.has_unpriced ? " · partial" : null}
      </span>
    ),
  });
  return chips;
}
