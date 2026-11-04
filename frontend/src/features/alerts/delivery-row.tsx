import { Link } from "@tanstack/react-router";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell/project-context";
import { errorMessage, isApiError, type Delivery } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  attemptsLabel,
  canRetry,
  deliveryEventLabel,
  deliveryInsightTarget,
  deliveryStatus,
  deliveryTitle,
  nextAttemptLabel,
  shortTime,
} from "./delivery-status";
import { useRetryDelivery } from "./channels-queries";

interface DeliveryRowProps {
  delivery: Delivery;
  channelId: string;
  /** `alerts:write`: only then does a failed row offer Retry. */
  canWrite: boolean;
}

/**
 * Figma "Alerts/Delivery row": status pill, what was sent, the event, when; attempts and the next
 * attempt; the last error, with Retry on a failed row for people who may change alerts.
 */
export function DeliveryRow({ delivery, channelId, canWrite }: DeliveryRowProps) {
  const retry = useRetryDelivery(channelId);
  const status = deliveryStatus(delivery);
  const StatusIcon = status.icon;
  const title = deliveryTitle(delivery);
  const target = deliveryInsightTarget(delivery);
  const { orgId } = useProjectParams();
  const event = deliveryEventLabel(delivery);
  const next = nextAttemptLabel(delivery);
  const failed = delivery.status === "failed";
  const showRetry = canWrite && canRetry(delivery);

  return (
    <li className="flex flex-col gap-3 rounded-tile bg-surface-muted p-4">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <Badge variant={status.tone} size="sm">
          <StatusIcon aria-hidden />
          {status.label}
        </Badge>
        <p className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
          {target && title ? (
            <Link
              to="/$orgId/$projectId/doctor/$insightId"
              params={{ orgId, projectId: target.projectId, insightId: target.insightId }}
              className="rounded-sm hover:underline"
            >
              {title}
            </Link>
          ) : (
            (title ?? <span className="font-medium text-muted-foreground">No summary kept</span>)
          )}
        </p>
        {event ? <span className="text-xs font-medium text-muted-foreground">{event}</span> : null}
        <time
          dateTime={delivery.created_at}
          title={formatTimestamp(delivery.created_at) ?? undefined}
          className="font-mono text-xs text-muted-foreground tabular"
        >
          {shortTime(delivery.created_at)}
        </time>
      </div>
      <dl className="flex gap-8">
        <div className="flex flex-col gap-0.5">
          <dt className="text-overline text-subtle-foreground uppercase">Attempts</dt>
          <dd className="text-sm text-foreground tabular">{attemptsLabel(delivery)}</dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="text-overline text-subtle-foreground uppercase">Next attempt</dt>
          <dd className="text-sm text-foreground">{next ?? "—"}</dd>
        </div>
      </dl>
      {delivery.last_error || showRetry ? (
        <div className="flex items-center gap-3">
          {delivery.last_error ? (
            <div className="min-w-0 flex-1 rounded-input bg-surface px-3 py-2">
              <p className="text-overline text-subtle-foreground uppercase">Last error</p>
              <p
                className={cn(
                  "font-mono text-xs [overflow-wrap:anywhere]",
                  failed ? "text-danger-text" : "text-muted-foreground",
                )}
              >
                {delivery.last_error}
              </p>
            </div>
          ) : (
            <span className="flex-1" />
          )}
          {showRetry ? (
            <Button
              size="sm"
              loading={retry.isPending}
              aria-label={`Retry ${title ?? "this delivery"}`}
              onClick={() => {
                retry.mutate(delivery.id, {
                  onSuccess: () => toast.success("Queued for retry."),
                  onError: (error) => {
                    toast.error(
                      isApiError(error) && error.code === "NOT_RETRYABLE"
                        ? `${errorMessage(error)} The log has been refreshed.`
                        : errorMessage(error),
                    );
                  },
                });
              }}
            >
              Retry
            </Button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
