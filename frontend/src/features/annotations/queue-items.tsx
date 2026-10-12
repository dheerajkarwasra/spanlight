import { ClipboardCheck, Info } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { SectionCard } from "@/components/section-card";
import { TileListSkeleton } from "@/components/tile-list";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { useMe } from "@/features/auth";
import type { AnnotationQueue, QueueItemStatus } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useMembersQuery, useQueueItemsQuery } from "./annotation-queries";
import { ITEM_COLUMNS, QueueItemRow, type MemberNames } from "./queue-item-row";
import { progressTotal } from "./queue-format";

type Filter = "all" | QueueItemStatus;

interface QueueItemsProps {
  queue: AnnotationQueue;
  /** The "Add items" button for the empty state; null for a read-only viewer. */
  addButton: ReactNode;
}

/** Figma "Review — Queue detail" Items card: the queue's items, filtered by status. */
export function QueueItems({ queue, addButton }: QueueItemsProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const query = useQueueItemsQuery(queue.id, filter === "all" ? undefined : filter);
  const me = useMe();
  const members = useMembersQuery();
  const names = useMemo<MemberNames>(
    () =>
      members.data !== undefined
        ? new Map(members.data.map((member) => [member.user.id, member.user.name]))
        : members.isError
          ? "unavailable"
          : "loading",
    [members.data, members.isError],
  );
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const { pending, done, skipped } = queue.progress;
  const total = progressTotal(queue.progress);
  const n = (value: number) => formatInteger(value) ?? String(value);

  return (
    <SectionCard
      title="Items"
      description={`${pluralize(total, "item")}${query.data ? ` · showing ${n(items.length)}` : ""}`}
      className="gap-4"
      actions={
        total === 0 ? null : (
          <SegmentedControl<Filter>
            aria-label="Item status"
            tone="surface"
            value={filter}
            onValueChange={setFilter}
            options={[
              { value: "all", label: `All ${n(total)}` },
              { value: "pending", label: `Pending ${n(pending)}` },
              { value: "done", label: `Done ${n(done)}` },
              { value: "skipped", label: `Skipped ${n(skipped)}` },
            ]}
            className="max-w-full overflow-x-auto"
          />
        )
      }
    >
      {query.isPending ? (
        <TileListSkeleton label="Loading items" rows={5} className="h-[58px]" />
      ) : query.data === undefined ? (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load items"
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title={total === 0 ? "No items in this queue" : "No items with this status"}
          description={
            total === 0
              ? "Add traces from the Traces page with “Add to queue”, or add them here by trace id."
              : "Pick another status to see the rest of the queue."
          }
          action={total === 0 ? (addButton ?? undefined) : undefined}
          className="rounded-tile bg-surface-muted"
        />
      ) : (
        <div className="flex flex-col gap-1.5">
          <div
            aria-hidden
            className={cn(
              ITEM_COLUMNS,
              "hidden px-5 py-2 text-overline font-semibold text-subtle-foreground uppercase",
            )}
          >
            <span>Trace</span>
            <span>Reviewing</span>
            <span>Status</span>
            <span>Reviewer</span>
            <span>Updated</span>
          </div>
          <ul className="flex flex-col gap-1.5" aria-label="Queue items">
            {items.map((item) => (
              <li key={item.id}>
                <QueueItemRow item={item} myUserId={me.user.id} names={names} />
              </li>
            ))}
          </ul>
          {query.hasNextPage ? (
            <ListFooter
              layout="card"
              summary={`Showing ${pluralize(items.length, "item")}`}
              hasNextPage
              isFetchingNextPage={query.isFetchingNextPage}
              onLoadMore={() => {
                void query.fetchNextPage();
              }}
            />
          ) : null}
          <p className="flex items-center gap-2 px-1 pt-2 text-xs font-medium text-muted-foreground">
            <Info aria-hidden className="size-3.5 shrink-0" />
            An item you claimed shows as assigned to you. Idle claims return to pending after 30
            minutes.
          </p>
        </div>
      )}
    </SectionCard>
  );
}
