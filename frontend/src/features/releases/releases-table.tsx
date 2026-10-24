import { getRouteApi, Link } from "@tanstack/react-router";
import { ChevronRight, Tag } from "lucide-react";

import { CostValue } from "@/components/cost-value";
import { DataTable, type DataColumn } from "@/components/data-table";
import { RelativeTime } from "@/components/relative-time";
import { SectionCard } from "@/components/section-card";
import { UnknownValue } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell";
import type { ReleaseStats } from "@/lib/api";
import {
  formatDuration,
  formatInteger,
  formatPercent,
  formatRelativeTime,
  pluralize,
} from "@/lib/format";
import { cn } from "@/lib/utils";

import { NO_CALLS_REASON, NO_COST_REASON } from "./release-deltas";

const releasesRoute = getRouteApi("/_authed/$orgId/$projectId/releases");

/** The Traces list filtered to one release, in the page's range and environment. */
function useTracesLink() {
  const { orgId, projectId } = useProjectParams();
  const { range, from, to, env } = releasesRoute.useSearch();
  return (release: string) =>
    ({
      to: "/$orgId/$projectId/traces",
      params: { orgId, projectId },
      search: { range, from, to, env, release },
    }) as const;
}

function ReleaseLink({ release }: { release: string }) {
  const link = useTracesLink();
  return (
    <Link
      {...link(release)}
      title={release}
      className="truncate rounded-sm font-mono text-label font-semibold text-foreground hover:underline"
    >
      {release}
    </Link>
  );
}

function Known({ text }: { text: string | null }) {
  return text === null ? <UnknownValue reason={NO_CALLS_REASON} /> : <>{text}</>;
}

function Cost({ release }: { release: ReleaseStats }) {
  return (
    <CostValue
      cost={release.cost_usd}
      hasUnpriced={release.unpriced_calls > 0}
      unknownReason={release.llm_calls === 0 ? NO_CALLS_REASON : NO_COST_REASON}
    />
  );
}

/** Plain text: the tile is one link, so it holds no focusable cost or tooltip. */
function tileMeta(release: ReleaseStats): string {
  const traces = pluralize(release.traces, "trace");
  const seen = formatRelativeTime(release.last_seen_at);
  return seen === null ? traces : `${traces} · last seen ${seen}`;
}

const COLUMNS: DataColumn<ReleaseStats>[] = [
  {
    id: "release",
    header: "Release",
    width: "minmax(200px,1fr)",
    sticky: true,
    cell: (release) => <ReleaseLink release={release.release} />,
  },
  {
    id: "first_seen",
    header: "First seen",
    width: "130px",
    hideable: true,
    cell: (release) => <RelativeTime iso={release.first_seen_at} />,
  },
  {
    id: "last_seen",
    header: "Last seen",
    width: "130px",
    hideable: true,
    cell: (release) => <RelativeTime iso={release.last_seen_at} />,
  },
  {
    id: "traces",
    header: "Traces",
    width: "100px",
    align: "end",
    cell: (release) => formatInteger(release.traces),
  },
  {
    id: "error_rate",
    header: "Error rate",
    width: "100px",
    align: "end",
    hideable: true,
    cell: (release) => <Known text={formatPercent(release.error_rate)} />,
  },
  {
    id: "p95",
    header: "p95",
    width: "100px",
    align: "end",
    hideable: true,
    cell: (release) => <Known text={formatDuration(release.p95_ms)} />,
  },
  {
    id: "cost",
    header: "Cost",
    width: "110px",
    align: "end",
    cell: (release) => <Cost release={release} />,
  },
];

/**
 * Spec §4.4 "Releases": every release in the window with its stats, each linking to its traces.
 * The shared table at `md` and up, tiles below. The page shows the list's loading, error and empty
 * states, so this renders only with releases.
 */
export function ReleasesTable({
  releases,
  stale,
}: {
  releases: ReleaseStats[];
  /** The rows are the previous window's while the new one loads: the table dims them. */
  stale: boolean;
}) {
  const link = useTracesLink();
  return (
    <SectionCard
      title="Releases"
      description={`${pluralize(releases.length, "release")} in this window, latest first`}
      className="gap-3"
    >
      <DataTable<ReleaseStats>
        id="releases"
        label="Releases"
        columns={COLUMNS}
        rows={releases}
        rowKey={(release) => release.release}
        rowHref={(release) => link(release.release)}
        empty={null}
        loading={stale}
      />
      <ul
        aria-label="Releases"
        aria-busy={stale || undefined}
        className={cn("flex flex-col gap-1.5 transition-opacity md:hidden", stale && "opacity-60")}
      >
        {releases.map((release) => (
          <li key={release.release}>
            <Link
              {...link(release.release)}
              className="flex items-center gap-3 rounded-tile bg-surface-muted px-4 py-3 transition-colors hover:bg-surface-hover"
            >
              <Tag aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate font-mono text-label font-semibold text-foreground">
                  {release.release}
                </span>
                <span className="text-xs font-medium text-muted-foreground">
                  {tileMeta(release)}
                </span>
              </span>
              <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
            </Link>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}
