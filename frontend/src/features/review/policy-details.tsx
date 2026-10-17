import { Link } from "@tanstack/react-router";
import {
  CalendarDays,
  CircleCheck,
  Clock,
  Info,
  Link2,
  List,
  Pause,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { useId, type ReactNode } from "react";

import { ReadOnlyNote } from "@/components/read-only-note";
import { Badge } from "@/components/ui/badge";
import { Card, CardTitle } from "@/components/ui/card";
import { useProjectParams } from "@/features/shell";
import type { ReviewPolicy } from "@/lib/api";
import { pluralize } from "@/lib/format";
import { roleBlockedCopy } from "@/lib/permissions";

import { PolicyActions } from "./policy-actions";
import { PolicyReasonTiles } from "./policy-reason-tiles";
import { lastSelectionText, nextRunText } from "./policy-summary";

interface PolicyDetailsProps {
  policy: ReviewPolicy;
  canWrite: boolean;
}

/** Figma "Review policy card / enabled" and "/ disabled": what the policy selects, and when. */
export function PolicyDetails({ policy, canWrite }: PolicyDetailsProps) {
  const titleId = useId();
  const { orgId, projectId } = useProjectParams();
  const environments =
    policy.environments.length === 0
      ? "All (internal ones excluded)"
      : policy.environments.join(", ");

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-5 p-5 sm:p-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between lg:gap-6">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle id={titleId}>Daily review</CardTitle>
            {policy.enabled ? (
              <Badge variant="success">
                <CircleCheck aria-hidden />
                Active
              </Badge>
            ) : (
              <Badge variant="outline">
                <Pause aria-hidden />
                Paused
              </Badge>
            )}
          </div>
          <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-muted-foreground">
            Fills the queue
            <Link
              to="/$orgId/$projectId/queues/$queueId"
              params={{ orgId, projectId, queueId: policy.queue_id }}
              className="inline-flex min-w-0 items-center gap-1 [overflow-wrap:anywhere] text-accent hover:underline"
            >
              <Link2 aria-hidden className="size-3.5 shrink-0" />
              {policy.queue_name}
            </Link>
          </p>
        </div>
        {canWrite ? <PolicyActions policy={policy} /> : null}
      </div>
      <PolicyReasonTiles policy={policy} canWrite={canWrite} />
      <dl className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
        <Fact icon={List} label="Daily cap" value={pluralize(policy.daily_cap, "trace")} />
        <Fact icon={SlidersHorizontal} label="Environments" value={environments} />
      </dl>
      <div className="flex flex-col gap-2 border-t border-border pt-4 text-sm">
        <Line icon={Clock} strong>
          {nextRunText(policy)}
        </Line>
        <Line icon={CalendarDays}>
          {lastSelectionText(policy.last_selection) ?? "No day selected yet."}
        </Line>
        {policy.enabled ? null : (
          <Line icon={Info}>
            Nothing is selected while paused. Resume to select again from the next night; use
            “Select a day…” for a missed day.
          </Line>
        )}
      </div>
      {canWrite ? null : <ReadOnlyNote>{roleBlockedCopy("change the daily review")}</ReadOnlyNote>}
    </Card>
  );
}

function Fact({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <Icon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="min-w-0 font-semibold [overflow-wrap:anywhere] text-foreground">{value}</dd>
    </div>
  );
}

function Line({
  icon: Icon,
  strong = false,
  children,
}: {
  icon: LucideIcon;
  strong?: boolean;
  children: ReactNode;
}) {
  return (
    <p
      className={
        strong
          ? "flex items-start gap-2 font-semibold text-foreground"
          : "flex items-start gap-2 text-muted-foreground"
      }
    >
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0">{children}</span>
    </p>
  );
}
