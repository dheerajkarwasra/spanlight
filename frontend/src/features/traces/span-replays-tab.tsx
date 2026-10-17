import { Link } from "@tanstack/react-router";
import { ArrowUpRight, CircleAlert, CircleCheck, CircleDashed, PencilLine } from "lucide-react";

import { CostValue } from "@/components/cost-value";
import { ErrorState } from "@/components/error-state";
import { RelativeTime } from "@/components/relative-time";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { UnknownValue } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell";
import type { Member, ReplayFidelity, ReplaySummary } from "@/lib/api";

import { useReplayMembersQuery, useSpanReplaysQuery } from "./replay-queries";

/** Chips carry an icon and text, never colour alone. */
const FIDELITY_CHIPS: Record<
  ReplayFidelity,
  { label: string; icon: typeof CircleCheck; tone: "success" | "neutral" | "warning" }
> = {
  exact: { label: "Exact", icon: CircleCheck, tone: "success" },
  modified: { label: "Modified", icon: PencilLine, tone: "neutral" },
  messages_only: { label: "Not exact", icon: CircleDashed, tone: "warning" },
};

/** The org's members to name who ran each replay, or why they can't be named yet. */
type Members = Member[] | "loading" | "unavailable";

const RUNNING_COST_REASON = "Not settled yet: the replay is still running.";
const NO_RESULT_REASON = "No result trace: the replay is running, or its call was not stored.";

/** Figma "Span panel / Replays tab": this span's replays, newest first. */
export function SpanReplaysTab({ traceId, spanId }: { traceId: string; spanId: string }) {
  const replays = useSpanReplaysQuery(traceId, spanId);
  const membersQuery = useReplayMembersQuery();
  const members: Members = membersQuery.isError ? "unavailable" : (membersQuery.data ?? "loading");

  if (replays.isPending) {
    return (
      <div role="status" aria-label="Loading replays" className="flex flex-col gap-2">
        <Skeleton className="h-[68px] rounded-tile" />
        <Skeleton className="h-[68px] rounded-tile" />
      </div>
    );
  }
  if (replays.isError) {
    return (
      <ErrorState
        compact
        error={replays.error}
        title="Couldn't load the replays"
        onRetry={() => {
          void replays.refetch();
        }}
      />
    );
  }
  if (replays.data.items.length === 0) {
    return (
      <p className="rounded-tile bg-surface-muted px-4 py-3.5 text-sm text-muted-foreground">
        No replays of this span yet.
      </p>
    );
  }
  return (
    <ul aria-label="Replays of this span" className="flex flex-col gap-2">
      {replays.data.items.map((replay) => (
        <ReplayRow key={replay.replay_id} replay={replay} members={members} />
      ))}
    </ul>
  );
}

function ReplayRow({ replay, members }: { replay: ReplaySummary; members: Members }) {
  const chip = FIDELITY_CHIPS[replay.fidelity];
  const running = replay.status === "running";
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-tile border border-border bg-surface px-4 py-3">
      <div className="flex min-w-0 items-center gap-2">
        <Badge size="sm" variant={chip.tone}>
          <chip.icon aria-hidden />
          {chip.label}
        </Badge>
        {replay.status === "failed" ? (
          <Badge size="sm" variant="danger" title={replay.error ?? undefined}>
            <CircleAlert aria-hidden />
            Failed
          </Badge>
        ) : null}
        <span title={replay.model} className="min-w-0 flex-1 truncate font-mono text-label">
          {replay.model}
        </span>
        {running ? (
          <UnknownValue reason={RUNNING_COST_REASON} />
        ) : (
          <CostValue cost={replay.cost_usd} className="text-sm font-semibold" />
        )}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
        <RelativeTime iso={replay.started_at} />
        <span aria-hidden>·</span>
        <ReplayAuthor userId={replay.requested_by} members={members} />
        <span className="ml-auto">
          <ResultTraceLink replay={replay} />
        </span>
      </div>
    </li>
  );
}

function ReplayAuthor({ userId, members }: { userId: string | null; members: Members }) {
  if (userId === null) {
    return <span>Former member</span>;
  }
  if (members === "loading") {
    return <Skeleton className="h-4 w-20 rounded-full" />;
  }
  if (members === "unavailable") {
    return <UnknownValue reason="Couldn't load the organization's members" />;
  }
  const member = members.find((candidate) => candidate.user.id === userId);
  return <span>{member ? member.user.name || member.user.email : "Former member"}</span>;
}

function ResultTraceLink({ replay }: { replay: ReplaySummary }) {
  const { orgId, projectId } = useProjectParams();
  const traceId = replay.result_trace_id;
  if (traceId === null) {
    return <UnknownValue reason={NO_RESULT_REASON} />;
  }
  return (
    <Link
      to="/$orgId/$projectId/traces/$traceId"
      params={{ orgId, projectId, traceId }}
      search={replay.result_span_id ? { span: replay.result_span_id } : {}}
      className="inline-flex items-center gap-1 rounded-sm font-semibold text-accent hover:underline"
    >
      Open result trace
      <ArrowUpRight aria-hidden className="size-3" />
    </Link>
  );
}
