import { Link } from "@tanstack/react-router";
import { ChevronRight, Info, OctagonAlert, TriangleAlert, type LucideIcon } from "lucide-react";
import { useId } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import { cn } from "@/lib/utils";

import { lastPassSummary, providerStatusLine, type StatusLine } from "./doctor-format";
import { DoctorScore } from "./doctor-score";
import { usedProviders } from "./incident-overlay";
import { attentionItems, type AttentionItem, type AttentionSeverity } from "./needs-attention";
import type { OverviewQueries } from "./use-overview-queries";

const SEVERITY: Record<AttentionSeverity, { icon: LucideIcon; className: string; word: string }> = {
  critical: { icon: OctagonAlert, className: "text-danger", word: "Critical" },
  warning: { icon: TriangleAlert, className: "text-warning", word: "Warning" },
  info: { icon: Info, className: "text-muted-foreground", word: "Info" },
};

const SOURCE_NAMES = {
  openInsights: "findings",
  alertRules: "alerts",
  budgets: "budgets",
} as const;

interface NeedsAttentionProps {
  queries: OverviewQueries;
  now: Date;
  className?: string;
}

/**
 * The "To do" list (spec §4): open Doctor findings, firing alert rules and budgets at 80 % or
 * more, with the last detector pass and the providers' incidents in the footer.
 */
export function NeedsAttention({ queries, now, className }: NeedsAttentionProps) {
  const titleId = useId();
  const sources = (["openInsights", "alertRules", "budgets"] as const).map((key) => ({
    key,
    query: queries[key],
  }));
  const pending = sources.some(({ query }) => query.isPending);
  const failed = sources.filter(({ query }) => query.isError);
  const items = attentionItems({
    insights: queries.openInsights.data?.items ?? [],
    rules: queries.alertRules.data ?? [],
    budgets: queries.budgets.data ?? [],
    now,
  });

  return (
    <Card
      role="region"
      aria-labelledby={titleId}
      className={cn("@container flex min-w-0 flex-col gap-3 p-5 sm:p-6", className)}
    >
      <CardTitle id={titleId}>Needs attention</CardTitle>
      {pending ? (
        <div aria-hidden className="flex flex-col gap-2">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-11 w-full" />
          ))}
        </div>
      ) : items.length > 0 ? (
        <ul className="flex flex-col">
          {items.map((item) => (
            <li key={`${item.kind}-${item.id}`} className="border-b border-border last:border-b-0">
              <AttentionRow item={item} />
            </li>
          ))}
        </ul>
      ) : failed.length === 0 ? (
        <p className="py-4 text-sm font-medium text-muted-foreground">Nothing needs attention.</p>
      ) : null}
      {failed.length > 0 ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground"
        >
          Couldn&apos;t load {failed.map(({ key }) => SOURCE_NAMES[key]).join(" and ")}.
          <Button
            size="sm"
            onClick={() => {
              for (const { query } of failed) {
                void query.refetch();
              }
            }}
          >
            Try again
          </Button>
        </div>
      ) : null}
      <Footer queries={queries} now={now} />
    </Card>
  );
}

function AttentionRow({ item }: { item: AttentionItem }) {
  const { orgId, projectId } = useProjectParams();
  const severity = SEVERITY[item.severity];
  const Icon = severity.icon;
  return (
    <Link
      to={item.to}
      params={{ orgId, projectId, ...item.params } as never}
      className="-mx-2 flex min-h-11 items-center gap-3 rounded-tile px-2 py-2.5 hover:bg-surface-hover"
    >
      <Icon aria-hidden className={cn("size-4 shrink-0", severity.className)} />
      <span className="sr-only">{severity.word}: </span>
      <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
        {item.title}
      </span>
      <span className="shrink-0 text-xs font-medium text-muted-foreground tabular">
        {item.meta}
      </span>
      <ChevronRight aria-hidden className="size-4 shrink-0 text-subtle-foreground" />
    </Link>
  );
}

/** "Last check 12 min ago · …" and the providers' open incidents, each when known. */
function Footer({ queries, now }: { queries: OverviewQueries; now: Date }) {
  const { detectorRuns, incidents, models, health, overview } = queries;
  const lines: StatusLine[] = [];
  if (detectorRuns.data) {
    lines.push(lastPassSummary(detectorRuns.data, now));
  }
  if (incidents.data && models.data) {
    lines.push(providerStatusLine(incidents.data, usedProviders(models.data)));
  }
  const score = health.data && overview.data;
  // Nothing loaded yet (or nothing known): no empty bordered strip.
  if (!score && lines.length === 0) {
    return null;
  }
  return (
    <div className="mt-auto flex flex-col gap-3 border-t border-border pt-3">
      {health.data && overview.data ? (
        <DoctorScore
          health={health.data}
          metrics={overview.data.current}
          hasPass={detectorRuns.data === undefined || detectorRuns.data.length > 0}
        />
      ) : null}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-medium text-muted-foreground">
        {lines.map((line) => (
          <p key={line.text} className="flex items-center gap-2">
            <span aria-hidden className={cn("size-2 shrink-0 rounded-full", TONE_DOT[line.tone])} />
            {line.text}
          </p>
        ))}
      </div>
    </div>
  );
}

const TONE_DOT: Record<StatusLine["tone"], string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  none: "bg-border-strong",
};
