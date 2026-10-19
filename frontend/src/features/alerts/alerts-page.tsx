import { BellRing } from "lucide-react";
import { useMemo } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ReadOnlyNote } from "@/components/read-only-note";
import { SectionCard } from "@/components/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission } from "@/features/shell";
import { viewOnlyCopy } from "@/lib/permissions";
import { useNow } from "@/lib/use-now";

import { AlertsLayout } from "./alerts-layout";
import { useAlertChannelsQuery, useAlertRulesQuery, useOpenEventsQuery } from "./alerts-queries";
import { CreateRuleButton } from "./create-rule-button";
import { RuleList, RuleListSkeleton } from "./rule-list";
import { rulesCountLine } from "./rule-state";

const EMPTY_BODY =
  "Get told when error rate, latency or cost crosses a line, before anyone opens a dashboard.";

/** Figma "Alerts — Rules": the project's alert rules with their live state. */
export function AlertsPage() {
  const rulesQuery = useAlertRulesQuery();
  const canWrite = usePermission("alerts:write");

  return (
    <AlertsLayout>
      <SectionCard
        title="Alert rules"
        description={<CountLine query={rulesQuery} />}
        actions={canWrite ? <CreateRuleButton className="max-sm:flex-1" /> : null}
        className="gap-3"
      >
        {canWrite ? null : <ReadOnlyNote>{viewOnlyCopy("alerts", "alerts:write")}</ReadOnlyNote>}
        <RulesContent query={rulesQuery} canWrite={canWrite} />
      </SectionCard>
    </AlertsLayout>
  );
}

type RulesQuery = ReturnType<typeof useAlertRulesQuery>;

function CountLine({ query }: { query: RulesQuery }) {
  if (query.isPending) {
    return <Skeleton className="mt-1 h-3 w-40" />;
  }
  return query.data === undefined ? null : rulesCountLine(query.data);
}

function RulesContent({ query, canWrite }: { query: RulesQuery; canWrite: boolean }) {
  if (query.isPending) {
    return <RuleListSkeleton />;
  }
  // A failed background refresh keeps the loaded list on screen; polling goes on.
  if (query.data === undefined) {
    return (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load alert rules"
        className="rounded-tile bg-surface-muted py-12"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  if (query.data.length === 0) {
    return (
      <EmptyState
        icon={BellRing}
        title="No alert rules"
        description={EMPTY_BODY}
        action={canWrite ? <CreateRuleButton /> : undefined}
        className="rounded-tile bg-surface-muted"
      />
    );
  }
  return <LoadedRules rules={query.data} />;
}

function LoadedRules({ rules }: { rules: NonNullable<RulesQuery["data"]> }) {
  const channelsQuery = useAlertChannelsQuery();
  const openEventsQuery = useOpenEventsQuery();
  const now = useNow();
  const channels = useMemo(
    () =>
      channelsQuery.data
        ? new Map(channelsQuery.data.map((channel) => [channel.id, channel]))
        : undefined,
    [channelsQuery.data],
  );
  const acknowledged = useMemo(
    () =>
      new Set(
        (openEventsQuery.data ?? [])
          .filter((event) => event.acknowledged_at !== null)
          .map((event) => event.rule_id),
      ),
    [openEventsQuery.data],
  );

  return <RuleList rules={rules} channels={channels} acknowledged={acknowledged} now={now} />;
}
