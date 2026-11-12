import { BellRing } from "lucide-react";
import { useMemo } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { AlertEvent, AlertRule } from "@/lib/api";

import type { useRuleEventsQuery } from "./alerts-queries";
import { EventItem } from "./event-item";
import { timelineMarkers } from "./timeline-markers";

interface EventTimelineProps {
  query: ReturnType<typeof useRuleEventsQuery>;
  rule: AlertRule;
  now: Date;
}

/**
 * Figma "Events": the rule's fire, resolve and acknowledge moments, most recent first, a page of
 * events at a time. The card keeps its place while the list loads, fails or is empty.
 */
export function EventTimeline({ query, rule, now }: EventTimelineProps) {
  const events = useMemo(() => query.data?.pages.flatMap((page) => page.items), [query.data]);
  const empty = events !== undefined && events.length === 0;

  return (
    <SectionCard
      title="Events"
      description={
        empty
          ? "Fired, resolved and acknowledged events"
          : "Most recent first. Times are relative; hover for the exact time."
      }
      className="gap-3.5"
    >
      {query.isPending ? <TimelineSkeleton /> : null}
      {query.isError && events === undefined ? (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load events"
          className="rounded-tile bg-surface-muted"
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : null}
      {empty ? (
        <EmptyState
          icon={BellRing}
          title="This rule hasn't fired yet."
          description="Events appear here when the rule fires, resolves or is acknowledged."
          className="rounded-tile bg-surface-muted"
        />
      ) : null}
      {events !== undefined && !empty ? (
        <>
          <Markers events={events} rule={rule} now={now} />
          {query.hasNextPage ? (
            <Button
              size="sm"
              className="self-start"
              loading={query.isFetchingNextPage}
              onClick={() => {
                void query.fetchNextPage();
              }}
            >
              Load more
            </Button>
          ) : null}
          {query.isFetchNextPageError ? (
            <ErrorState
              compact
              error={query.error}
              title="Couldn't load more events"
              onRetry={() => void query.fetchNextPage()}
            />
          ) : null}
        </>
      ) : null}
    </SectionCard>
  );
}

interface MarkersProps {
  events: readonly AlertEvent[];
  rule: AlertRule;
  now: Date;
}

function Markers({ events, rule, now }: MarkersProps) {
  const markers = timelineMarkers(events);
  return (
    <ol aria-label={`Events of ${rule.name}`} className="flex flex-col pt-2">
      {markers.map((marker, index) => (
        <EventItem
          key={marker.key}
          marker={marker}
          rule={rule}
          last={index === markers.length - 1}
          now={now}
        />
      ))}
    </ol>
  );
}

/** Figma "Rule detail — loading": round markers with two text lines each. */
function TimelineSkeleton() {
  return (
    <div role="status" aria-label="Loading events" className="flex flex-col gap-4 pt-2">
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index} className="flex items-center gap-3.5">
          <Skeleton className="size-8 rounded-full" />
          <div className="flex flex-col gap-1.5">
            <Skeleton className="h-3 w-40" />
            <Skeleton className="h-2.5 w-28" />
          </div>
        </div>
      ))}
    </div>
  );
}
