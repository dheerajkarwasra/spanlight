import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { DataTable, type DataColumn } from "@/components/data-table";
import { TileList } from "@/components/tile-list";
import { Skeleton } from "@/components/ui/skeleton";
import { UnknownValue } from "@/components/unknown-value";
import {
  BILLING_REASONS,
  TONE_ICONS,
  TONE_LABELS,
  varianceReason,
} from "@/features/attribution/cost-strip";
import { useProjectParams } from "@/features/shell";
import { varianceTone } from "@/features/traces";
import type { BillingSource } from "@/lib/api";
import { cn } from "@/lib/utils";

import { formatUsd, formatVariance } from "./reconciliation-format";
import { formatUtc, projectsLabel } from "./source-model";
import {
  figuresOf,
  SPANLIGHT_REASONS,
  type Days,
  type Figures,
  type Reason,
  type Row,
  type SourceItem,
} from "./source-summary";
import { windowFromDays, windowLabel } from "./window-picker";

/** A figure of the row: a skeleton while its report loads, else the value or "—" with a reason. */
function Figure(props: { figures: Figures; reason: Reason; children: ReactNode }) {
  const { figures, reason, children } = props;
  if (figures.state === "pending") {
    return <Skeleton aria-hidden className="inline-block h-4 w-14 align-middle" />;
  }
  if (figures.state === "unknown") {
    return <UnknownValue reason={figures.reason} />;
  }
  return children ?? <UnknownValue reason={reason(figures.aggregate)} />;
}

function Money({ figures, field }: { figures: Figures; field: "providerUsd" | "spanlightUsd" }) {
  const value = figures.state === "ready" ? figures.aggregate[field] : null;
  return (
    <Figure figures={figures} reason={SPANLIGHT_REASONS[field]}>
      {value === null ? null : formatUsd(value)}
    </Figure>
  );
}

/** "−4.2 %" with the band as icon and words (spec rulings 1 and 7, the strip's labels). */
function Variance({ figures }: { figures: Figures }) {
  const ratio = figures.state === "ready" ? figures.aggregate.variance : null;
  const tone = varianceTone(ratio);
  if (ratio === null || tone === "unknown") {
    return (
      <Figure figures={figures} reason={varianceReason}>
        {null}
      </Figure>
    );
  }
  const Icon = TONE_ICONS[tone].icon;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span className="tabular">{formatVariance(ratio.toFixed(6))}</span>
      <Icon aria-hidden className={cn("size-3.5 shrink-0", TONE_ICONS[tone].color)} />
      <span className="text-muted-foreground">{TONE_LABELS[tone]}</span>
    </span>
  );
}

function SourceLink({ source, days }: { source: BillingSource; days: Days }) {
  const params = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/settings/billing-sources/$sourceId"
      params={{ ...params, sourceId: source.id }}
      search={days ?? undefined}
      className="truncate font-semibold text-foreground underline-offset-4 hover:underline"
    >
      {source.name}
    </Link>
  );
}

function columns(days: Days): DataColumn<Row>[] {
  const span = days ? windowLabel(windowFromDays(days.first, days.last)) : null;
  return [
    {
      id: "source",
      header: "Source",
      width: "minmax(160px,1fr)",
      sticky: true,
      cell: ({ source }) => <SourceLink source={source} days={days} />,
    },
    { id: "provider", header: "Provider", width: "100px", cell: ({ source }) => source.provider },
    {
      id: "projects",
      header: "Projects",
      width: "100px",
      cell: ({ source }) => projectsLabel(source.projects.length),
    },
    {
      id: "window",
      header: "Window",
      width: "170px",
      cell: () => span ?? <UnknownValue reason={BILLING_REASONS.noWindow} />,
    },
    {
      id: "provider_usd",
      header: "Provider $",
      width: "110px",
      align: "end",
      cell: ({ figures }) => <Money figures={figures} field="providerUsd" />,
    },
    {
      id: "spanlight_usd",
      header: "Spanlight $",
      width: "110px",
      align: "end",
      cell: ({ figures }) => <Money figures={figures} field="spanlightUsd" />,
    },
    {
      id: "variance",
      header: "Variance",
      width: "170px",
      cell: ({ figures }) => <Variance figures={figures} />,
    },
    {
      id: "fetched",
      header: "Last fetched",
      width: "150px",
      align: "end",
      cell: ({ source }) =>
        source.last_fetched_at === null ? (
          <UnknownValue reason="Not fetched yet." />
        ) : (
          formatUtc(source.last_fetched_at)
        ),
    },
  ];
}

/**
 * Spec §3.5 sources table: each billing source that maps this project, over the report window,
 * with the provider's bill, Spanlight's figure and the variance; the name opens the report. The
 * shared table from `md`, tiles below.
 */
export function SourceSummaryTable({ items, days }: { items: SourceItem[]; days: Days }) {
  const rows = items.map((item) => ({ ...item, figures: figuresOf(item, days) }));
  return (
    <>
      <DataTable<Row>
        id="reconciliation-sources"
        label="Billing sources for this project"
        columns={columns(days)}
        rows={rows}
        rowKey={({ source }) => source.id}
        loading={false}
        empty={null}
      />
      <TileList label="Billing sources for this project" className="md:hidden">
        {rows.map((item) => (
          <li
            key={item.source.id}
            className="flex flex-col gap-1 rounded-tile bg-surface-muted px-4 py-3 text-sm"
          >
            <SourceLink source={item.source} days={days} />
            <p className="text-xs text-muted-foreground">
              {item.source.provider} · {projectsLabel(item.source.projects.length)}
            </p>
            <p className="flex flex-wrap gap-x-3 tabular">
              <span>
                Provider <Money figures={item.figures} field="providerUsd" />
              </span>
              <span>
                Spanlight <Money figures={item.figures} field="spanlightUsd" />
              </span>
            </p>
            <Variance figures={item.figures} />
          </li>
        ))}
      </TileList>
    </>
  );
}
