import { useQuery } from "@tanstack/react-query";

import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { UnknownValue } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell/project-context";
import { ingestQuotaApi, queryKeys } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  EXHAUSTED_TEXT,
  FOOTER_LINES,
  NO_QUOTA_REASON,
  RESETS_TEXT,
  quotaRows,
  rateLine,
  sourceLabel,
  type QuotaRow,
} from "./ingest-quota-format";

const UNLIMITED_RATE_REASON = "No ingest rate limit on this instance";

/**
 * Figma "Settings/Ingest quota card": today's spans and bytes against the project's daily quotas,
 * a meter each, the rate limit, and where each cap comes from. Unlimited reads "—" with a reason,
 * never 0; a used-up quota says so in words, not only in the meter's colour.
 */
export function IngestQuotaCard() {
  const { projectId } = useProjectParams();
  const query = useQuery({
    queryKey: queryKeys.project(projectId).ingestQuota({}),
    queryFn: ({ signal }) => ingestQuotaApi.get(projectId, {}, signal),
  });
  const description =
    "What this project has sent through the SDK and OTLP today, against its daily quota.";

  if (query.isPending) {
    return (
      <SectionCard title="Ingest quota" description={description}>
        <div role="status" aria-label="Loading ingest quota" className="flex flex-col gap-4">
          <Skeleton className="h-10 w-full rounded-tile" />
          <Skeleton className="h-10 w-full rounded-tile" />
        </div>
      </SectionCard>
    );
  }
  if (query.isError) {
    return (
      <SectionCard title="Ingest quota" description={description}>
        <ErrorState
          compact
          error={query.error}
          title="Ingest quota didn't load"
          onRetry={() => {
            void query.refetch();
          }}
        />
      </SectionCard>
    );
  }
  const rows = quotaRows(query.data);
  const exhausted = rows.some((row) => row.state === "exhausted");
  const rate = query.data.limits.spans_per_second;
  return (
    <SectionCard title="Ingest quota" description={description} className="gap-4">
      <dl className="flex flex-col gap-4">
        {rows.map((row) => (
          <QuotaRowView key={row.label} row={row} />
        ))}
        <div className="flex items-center gap-2">
          <dt className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            Rate limit
            <Badge>{sourceLabel(rate.source)}</Badge>
          </dt>
          <dd className="ml-auto text-sm font-semibold text-foreground tabular">
            {rateLine(rate.value) ?? <UnknownValue reason={UNLIMITED_RATE_REASON} />}
          </dd>
        </div>
      </dl>
      {exhausted ? (
        <p role="status" className="text-xs font-semibold text-danger-text">
          {EXHAUSTED_TEXT}
        </p>
      ) : rows.some((row) => row.state === "under") ? (
        <p className="text-xs font-medium text-muted-foreground">{RESETS_TEXT}</p>
      ) : null}
      <div className="flex flex-col gap-1 border-t border-border pt-4 text-xs font-medium text-muted-foreground">
        {FOOTER_LINES.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
    </SectionCard>
  );
}

// A dl group holds only dt and dd (axe `definition-list`, `dlitem`): the badge sits in the dt and the
// meter in a second dd, laid out by the grid.
function QuotaRowView({ row }: { row: QuotaRow }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1.5">
      <dt className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        {row.label}
        <Badge>{row.source}</Badge>
      </dt>
      <dd
        className={cn(
          "text-sm font-semibold tabular",
          row.state === "exhausted" ? "text-danger-text" : "text-foreground",
        )}
      >
        {row.text ?? <UnknownValue reason={NO_QUOTA_REASON} />}
      </dd>
      {row.ratio !== null ? (
        <dd className="col-span-2">
          <div
            role="meter"
            aria-label={row.label}
            aria-valuemin={0}
            aria-valuemax={1}
            aria-valuenow={row.ratio}
            aria-valuetext={row.text ?? undefined}
            className="h-1.5 w-full overflow-hidden rounded-full bg-surface-muted"
          >
            <div
              className={cn(
                "h-full rounded-full",
                row.state === "exhausted" ? "bg-danger" : "bg-accent",
              )}
              style={{ width: `${String(Math.max(1, row.ratio * 100))}%` }}
            />
          </div>
        </dd>
      ) : null}
    </div>
  );
}
