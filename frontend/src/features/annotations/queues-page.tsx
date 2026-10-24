import { ClipboardCheck, Info, Plus } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { PageHeader } from "@/components/page-header";
import { SectionCard } from "@/components/section-card";
import { TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { LiveDemoButton } from "@/features/auth";
import { ReviewPolicyCard } from "@/features/review";
import { ScoreSettingsLinks } from "@/features/scores";
import { usePermission } from "@/features/shell";
import { pluralize } from "@/lib/format";

import { useQueuesQuery, useScoreConfigsQuery } from "./annotation-queries";
import { CreateQueueDialog } from "./create-queue-dialog";
import { QueueList } from "./queue-list";

/**
 * Figma "Review — Queues list" and its states (empty, loading, error, 375 px). Every member reads
 * the queues; members and up create them.
 */
export function QueuesPage() {
  const canWrite = usePermission("annotations:write");
  const query = useQueuesQuery();
  const configs = useScoreConfigsQuery();
  const queues = query.data?.pages.flatMap((page) => page.items) ?? [];
  const waiting = queues.reduce((sum, queue) => sum + queue.progress.pending, 0);
  const more = query.hasNextPage ? "+" : "";

  const newQueue = (label: string) =>
    canWrite ? (
      <CreateQueueDialog
        configs={configs.data}
        trigger={
          <Button variant="primary">
            <Plus aria-hidden />
            {label}
          </Button>
        }
      />
    ) : null;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Review"
        description={
          <>
            Annotation queues hold traces for people to score by hand. Reviewers claim one item at a
            time. <ScoreSettingsLinks />
          </>
        }
        actions={newQueue("New queue")}
      />
      <ReviewPolicyCard
        queues={query.data === undefined ? undefined : queues}
        configs={configs.data}
      />
      <SectionCard
        title="Annotation queues"
        description={
          query.data === undefined
            ? undefined
            : `${pluralize(queues.length, "queue")}${more} · ${pluralize(waiting, "item")}${more} waiting`
        }
        className="gap-4"
      >
        <QueuesContent query={query} configs={configs.data} createButton={newQueue("New queue")} />
        {queues.length > 0 ? (
          <p className="flex items-center gap-2 px-1 pt-1 text-xs font-medium text-muted-foreground">
            <Info aria-hidden className="size-3.5 shrink-0" />A claim that is idle for 30 minutes
            goes back to the queue, so nobody can block it by walking away.
          </p>
        ) : null}
      </SectionCard>
    </div>
  );
}

interface QueuesContentProps {
  query: ReturnType<typeof useQueuesQuery>;
  configs: ReturnType<typeof useScoreConfigsQuery>["data"];
  createButton: ReactNode;
}

function QueuesContent({ query, configs, createButton }: QueuesContentProps) {
  if (query.isPending) {
    return <TileListSkeleton label="Loading queues" rows={4} className="h-[68px]" />;
  }
  if (query.data === undefined) {
    return (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load queues"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  const queues = query.data.pages.flatMap((page) => page.items);
  if (queues.length === 0) {
    return (
      <EmptyState
        icon={ClipboardCheck}
        title="No queues yet"
        description="Create a queue, then add traces from the Traces page. Reviewers score them one at a time with the keyboard."
        action={createButton ?? undefined}
        setup={<LiveDemoButton />}
        className="rounded-tile bg-surface-muted"
      />
    );
  }
  return (
    <>
      <QueueList queues={queues} configs={configs} />
      {query.hasNextPage ? (
        <ListFooter
          layout="card"
          summary={`Showing ${pluralize(queues.length, "queue")}`}
          hasNextPage
          isFetchingNextPage={query.isFetchingNextPage}
          onLoadMore={() => {
            void query.fetchNextPage();
          }}
        />
      ) : null}
    </>
  );
}
