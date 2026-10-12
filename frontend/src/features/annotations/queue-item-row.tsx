import { Link } from "@tanstack/react-router";
import { ChevronRight, Link2 } from "lucide-react";

import { RelativeTime } from "@/components/relative-time";
import { UnknownValue } from "@/components/unknown-value";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import type { QueueItem } from "@/lib/api";
import { shortId } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  itemStatus,
  itemUpdatedAt,
  reviewerOf,
  reviewingText,
  type ItemStatusTone,
} from "./queue-format";

export const ITEM_COLUMNS =
  "md:grid md:grid-cols-[minmax(0,1fr)_minmax(0,150px)_130px_minmax(0,130px)_96px_16px] md:items-center md:gap-4";

const STATUS_TONES: Record<ItemStatusTone, string> = {
  done: "border-success bg-success-subtle text-success",
  mine: "border-accent bg-accent-subtle text-accent-subtle-foreground",
  neutral: "border-border-strong bg-surface text-muted-foreground",
};

/** User id → name from the org's members, or why there are none to show yet. */
export type MemberNames = ReadonlyMap<string, string> | "loading" | "unavailable";

interface QueueItemRowProps {
  item: QueueItem;
  myUserId: string;
  /** A user id missing from the map is a former member. */
  names: MemberNames;
}

/** One row of the queue's item table; opens the trace (on the item's span when it has one). */
export function QueueItemRow({ item, myUserId, names }: QueueItemRowProps) {
  const { orgId, projectId } = useProjectParams();
  const status = itemStatus(item, myUserId);
  const reviewer = reviewerOf(item);
  return (
    <Link
      to="/$orgId/$projectId/traces/$traceId"
      params={{ orgId, projectId, traceId: item.trace_id }}
      search={item.span_id === null ? {} : { span: item.span_id }}
      className={cn(
        ITEM_COLUMNS,
        "flex flex-col gap-2 rounded-tile bg-surface-muted px-4 py-3 transition-colors hover:bg-surface-hover md:px-5",
      )}
    >
      <span className="flex items-center justify-between gap-2">
        <span className="inline-flex min-w-0 items-center gap-1.5 font-mono text-xs text-accent">
          <Link2 aria-hidden className="size-3 shrink-0" />
          <span className="sr-only">Trace </span>
          <span className="truncate">{shortId(item.trace_id, 12)}</span>
        </span>
        <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground md:hidden" />
      </span>
      <span className="text-label text-foreground">{reviewingText(item)}</span>
      <span>
        <span
          className={cn(
            "inline-flex rounded-full border px-2.5 py-0.5 text-2xs font-semibold",
            STATUS_TONES[status.tone],
          )}
        >
          {status.label}
        </span>
      </span>
      <span className="flex min-w-0 items-center gap-2 text-label text-foreground">
        <span className="text-xs text-muted-foreground md:sr-only">Reviewer </span>
        {reviewer === null ? (
          <span className="text-muted-foreground">
            <span aria-hidden>—</span>
            <span className="sr-only">none yet</span>
          </span>
        ) : (
          <ReviewerName names={names} userId={reviewer} />
        )}
      </span>
      <span className="text-xs font-medium text-muted-foreground">
        <span className="md:sr-only">Updated </span>
        <RelativeTime iso={itemUpdatedAt(item)} />
      </span>
      <ChevronRight aria-hidden className="hidden size-4 text-muted-foreground md:block" />
    </Link>
  );
}

function ReviewerName({ names, userId }: { names: MemberNames; userId: string }) {
  if (names === "loading") {
    return <Skeleton className="h-4 w-20 rounded-full" />;
  }
  if (names === "unavailable") {
    return <UnknownValue reason="Couldn't load the organization's members" />;
  }
  const name = names.get(userId) ?? null;
  const label = name ?? "Former member";
  return (
    <>
      <span
        aria-hidden
        className="flex size-5 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-2xs font-bold text-accent-subtle-foreground"
      >
        {label.charAt(0).toUpperCase()}
      </span>
      <span className={cn("truncate", name === null && "text-muted-foreground")}>{label}</span>
    </>
  );
}
