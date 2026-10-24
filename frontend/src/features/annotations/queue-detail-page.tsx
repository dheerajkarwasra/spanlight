import { getRouteApi, Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";

import { Breadcrumbs, Crumb } from "@/components/breadcrumbs";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission, useProjectParams } from "@/features/shell";
import { formatDate } from "@/lib/format";

import { useQueueQuery, useScoreConfigsQuery } from "./annotation-queries";
import { AddItemsDialog } from "./add-items-dialog";
import { DeleteQueueDialog } from "./delete-queue-dialog";
import { QueueItems } from "./queue-items";
import { QueueSummary } from "./queue-summary";

const queueRoute = getRouteApi("/_authed/$orgId/$projectId/queues/$queueId");

/** Figma "Review — Queue detail" and its states (no items, 375 px). */
export function QueueDetailPage() {
  const { queueId } = queueRoute.useParams();
  const { orgId, projectId } = useProjectParams();
  const canWrite = usePermission("annotations:write");
  const queue = useQueueQuery(queueId);
  const configs = useScoreConfigsQuery();

  const back = (current: string) => (
    <Breadcrumbs current={current}>
      <Crumb to="/$orgId/$projectId/queues" params={{ orgId, projectId }}>
        Review
      </Crumb>
    </Breadcrumbs>
  );

  if (queue.isPending) {
    return (
      <div role="status" aria-label="Loading the queue" className="flex flex-col gap-6">
        {back("Queue")}
        <Skeleton className="h-9 w-72 rounded-full" />
        <Skeleton className="h-40 rounded-card" />
        <Skeleton className="h-80 rounded-card" />
      </div>
    );
  }
  if (queue.isError) {
    return (
      <div className="flex flex-col gap-6">
        {back("Queue")}
        <ErrorState
          error={queue.error}
          title="Couldn't load this queue"
          onRetry={() => {
            void queue.refetch();
          }}
        />
      </div>
    );
  }

  const data = queue.data;
  const addButton = canWrite ? (
    <AddItemsDialog
      queueId={data.id}
      queueName={data.name}
      trigger={
        <Button>
          <Plus aria-hidden />
          Add items
        </Button>
      }
    />
  ) : null;
  const canStart = canWrite && data.progress.pending > 0;

  return (
    <div className="flex flex-col gap-6">
      {back(data.name)}
      <PageHeader
        title={data.name}
        description={`Created ${formatDate(data.created_at) ?? ""}`}
        actions={
          <>
            {canWrite ? <DeleteQueueDialog queue={data} /> : null}
            {addButton}
            {canStart ? (
              <Button variant="primary" asChild>
                <Link
                  to="/$orgId/$projectId/annotate/$queueId"
                  params={{ orgId, projectId, queueId: data.id }}
                >
                  Start reviewing
                </Link>
              </Button>
            ) : (
              <Button variant="primary" disabled>
                Start reviewing
              </Button>
            )}
          </>
        }
      />
      <QueueSummary queue={data} configs={configs.data} />
      <QueueItems queue={data} addButton={addButton} />
    </div>
  );
}
