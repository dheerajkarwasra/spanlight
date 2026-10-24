import { getRouteApi, Link } from "@tanstack/react-router";
import { CircleAlert } from "lucide-react";
import { useMemo } from "react";

import { Callout } from "@/components/callout";
import { ReadOnlyNote } from "@/components/read-only-note";
import { Button } from "@/components/ui/button";
import { usePermission, useProjectParams } from "@/features/shell";
import type { AlertRule } from "@/lib/api";
import { viewOnlyCopy } from "@/lib/permissions";
import { useNow } from "@/lib/use-now";

import { useAlertChannelsQuery, useAlertRuleQuery, useRuleEventsQuery } from "./alerts-queries";
import { EventTimeline } from "./event-timeline";
import { AlertsBreadcrumbs, RuleDetailHeader } from "./rule-detail-header";
import { RuleDetailSkeleton } from "./rule-detail-skeleton";
import { RuleSettingsCard } from "./rule-settings-card";
import { mutedLabel, mutedUntil } from "./rule-state";
import { RuleStateHero } from "./rule-state-hero";
import { openEvent } from "./timeline-markers";

const ruleRoute = getRouteApi("/_authed/$orgId/$projectId/alerts/$ruleId");

const MUTE_BODY = "The rule keeps evaluating and recording events, but sends no notifications.";

/** Figma "Alerts — Rule detail": one rule's live state, its event timeline and its settings. */
export function AlertRuleDetailPage() {
  const { ruleId } = ruleRoute.useParams();
  const ruleQuery = useAlertRuleQuery(ruleId);

  if (ruleQuery.isPending) {
    return <RuleDetailSkeleton />;
  }
  // A failed background refresh keeps the loaded rule on screen; polling goes on.
  if (ruleQuery.data === undefined) {
    return (
      <RuleLoadError
        onRetry={() => {
          void ruleQuery.refetch();
        }}
      />
    );
  }
  return <RuleDetail rule={ruleQuery.data} />;
}

function RuleDetail({ rule }: { rule: AlertRule }) {
  const canWrite = usePermission("alerts:write");
  const now = useNow();
  const eventsQuery = useRuleEventsQuery(rule.id);
  const channelsQuery = useAlertChannelsQuery();
  const channels = useMemo(
    () =>
      channelsQuery.data
        ? new Map(channelsQuery.data.map((channel) => [channel.id, channel]))
        : undefined,
    [channelsQuery.data],
  );
  // The newest event is the open one, if any: a rule has at most one open event.
  const firstPage = eventsQuery.data?.pages[0];
  const open = firstPage === undefined ? undefined : openEvent(firstPage.items);
  const until = mutedUntil(rule, now);

  return (
    <div className="flex flex-col gap-6">
      <RuleDetailHeader
        rule={rule}
        openEvent={open}
        eventsFailed={eventsQuery.isError && firstPage === undefined}
        canWrite={canWrite}
        now={now}
      />
      {canWrite ? null : <ReadOnlyNote>{viewOnlyCopy("alerts", "alerts:write")}</ReadOnlyNote>}
      {until === null ? null : (
        <Callout tone="warning" title={mutedLabel(until, now)}>
          {MUTE_BODY}
        </Callout>
      )}
      <RuleStateHero rule={rule} openEvent={open} now={now} />
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <EventTimeline query={eventsQuery} rule={rule} now={now} />
        <RuleSettingsCard rule={rule} channels={channels} />
      </div>
    </div>
  );
}

/** Figma "Rule detail — error": the rule is gone or the request failed. */
function RuleLoadError({ onRetry }: { onRetry: () => void }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className="flex flex-col gap-6">
      <AlertsBreadcrumbs current="Rule" />
      <div
        role="alert"
        className="flex flex-col items-center gap-4 rounded-card bg-surface-muted px-6 py-12 text-center"
      >
        <span className="flex size-12 items-center justify-center rounded-full bg-danger-subtle">
          <CircleAlert aria-hidden className="size-5 text-danger" strokeWidth={2} />
        </span>
        <div className="flex flex-col gap-1.5">
          <h1 className="text-xl font-bold tracking-[-0.01em] text-foreground">
            Couldn&rsquo;t load this rule
          </h1>
          <p className="text-sm text-muted-foreground">
            It may have been deleted, or the request failed.
          </p>
        </div>
        <div className="flex flex-wrap justify-center gap-2.5">
          <Button variant="primary" size="sm" onClick={onRetry}>
            Try again
          </Button>
          <Button size="sm" asChild>
            <Link to="/$orgId/$projectId/alerts" params={{ orgId, projectId }}>
              Back to alerts
            </Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
