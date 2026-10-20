import { FileText, Info, SearchX } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { formatInteger } from "@/lib/format";

import { AuditLogList } from "./audit-log-list";
import type { useAuditLogQuery } from "./audit-queries";

type AuditQuery = ReturnType<typeof useAuditLogQuery>;

interface AuditLogContentProps {
  query: AuditQuery;
  /** Whether a filter is set: an empty answer then means "no match", not "nothing happened yet". */
  filtered: boolean;
  onClearFilters: () => void;
}

/**
 * What sits under the filters: skeleton rows while loading, an error with a retry, the two empty
 * states (muted panels, as in Figma), or the events with "Load more" and a note on the CSV.
 */
export function AuditLogContent({ query, filtered, onClearFilters }: AuditLogContentProps) {
  if (query.isPending) {
    return <TileListSkeleton label="Loading audit log" rows={6} className="h-[59px]" />;
  }

  if (query.isError && !query.isFetchNextPageError) {
    return (
      <StatePanel>
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load the audit log"
          onRetry={() => {
            void query.refetch();
          }}
        />
      </StatePanel>
    );
  }

  const events = query.data.pages.flatMap((page) => page.items);

  if (events.length === 0) {
    return (
      <StatePanel>
        {filtered ? (
          <EmptyState
            icon={SearchX}
            title="No events match these filters"
            description="Try a wider date range, or clear the filters to see every event."
            action={
              <Button variant="primary" onClick={onClearFilters}>
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            icon={FileText}
            title="No events yet"
            description="Changes to members, invites, projects and API keys will appear here."
          />
        )}
      </StatePanel>
    );
  }

  const count = formatInteger(events.length) ?? String(events.length);

  return (
    <>
      <AuditLogList events={events} />
      <div className="flex flex-col items-center gap-2 pt-2 text-center">
        {query.isFetchNextPageError ? (
          <p role="alert" className="text-sm text-danger-text">
            Couldn&apos;t load more events. Try again.
          </p>
        ) : null}
        {query.hasNextPage ? (
          <Button
            loading={query.isFetchingNextPage}
            onClick={() => {
              void query.fetchNextPage();
            }}
          >
            Load more
          </Button>
        ) : null}
        <p className="text-xs font-medium text-subtle-foreground" aria-live="polite">
          {query.hasNextPage
            ? `Showing the ${count} most recent ${events.length === 1 ? "event" : "events"}`
            : `Showing all ${count} ${events.length === 1 ? "event" : "events"}. You've reached the beginning of the audit log.`}
        </p>
      </div>
      <p className="flex items-start gap-2 px-1 pt-1.5 text-xs font-medium text-muted-foreground">
        <Info aria-hidden className="mt-px size-3.5 shrink-0 text-subtle-foreground" />
        <span>
          Download exports every event that matches these filters, up to 50,000, as CSV or JSON
          Lines.
        </span>
      </p>
    </>
  );
}

/** The muted rounded panel Figma puts empty and error states in, inside the card. */
function StatePanel({ children }: { children: ReactNode }) {
  return <div className="rounded-tile bg-surface-muted">{children}</div>;
}
