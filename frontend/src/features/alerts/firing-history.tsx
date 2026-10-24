import { History } from "lucide-react";
import { useMemo } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { TileList, TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import type { AlertRule } from "@/lib/api";
import { useNow } from "@/lib/use-now";

import { useEventHistoryQuery } from "./alerts-queries";
import { FiringGroupRow } from "./firing-group-row";
import { groupFirings } from "./firing-groups";

/**
 * Spec ruling 18: every rule's firings across the project, one row per rule ("Fired 3 times ·
 * last 14 min ago"), expandable to the firings with their trace links. Counts are of the loaded
 * pages; "Load older" fetches the next 50 events.
 */
export function FiringHistory({ rules }: { rules: readonly AlertRule[] | undefined }) {
  const query = useEventHistoryQuery();
  const now = useNow();
  const ruleById = useMemo(() => new Map(rules?.map((rule) => [rule.id, rule])), [rules]);
  const groups = useMemo(
    () =>
      query.data
        ? groupFirings(
            query.data.pages.flatMap((page) => page.items),
            query.hasNextPage,
          )
        : undefined,
    [query.data, query.hasNextPage],
  );

  return (
    <SectionCard
      title="Firing history"
      description="Every rule's firings, latest first."
      className="gap-3"
    >
      {query.isPending ? <TileListSkeleton label="Loading firing history" /> : null}
      {query.isError && groups === undefined ? (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load firing history"
          className="rounded-tile bg-surface-muted"
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : null}
      {groups?.length === 0 ? (
        <EmptyState
          icon={History}
          title="Nothing has fired yet"
          description="Each time a rule or budget fires, it shows here."
          className="rounded-tile bg-surface-muted"
        />
      ) : null}
      {groups !== undefined && groups.length > 0 ? (
        <>
          <TileList label="Firings by rule">
            {groups.map((group) => (
              <FiringGroupRow
                key={group.ruleId}
                group={group}
                rule={ruleById.get(group.ruleId)}
                now={now}
              />
            ))}
          </TileList>
          {query.hasNextPage ? (
            <Button
              size="sm"
              className="self-start"
              loading={query.isFetchingNextPage}
              onClick={() => {
                void query.fetchNextPage();
              }}
            >
              Load older
            </Button>
          ) : null}
        </>
      ) : null}
    </SectionCard>
  );
}
