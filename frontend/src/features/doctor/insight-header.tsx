import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";

import { useProjectParams } from "@/features/shell";
import type { InsightDetail } from "@/lib/api";

import { InsightActions } from "./insight-actions";
import { compactRelative, formatDateTime } from "./insight-format";
import { KindChip } from "./kind-chip";
import { SeverityBadge } from "./severity-badge";
import { StatusPill } from "./status-pill";
import { StatusBanner } from "./status-banner";

/** "← Doctor": back to the list. */
export function BackToDoctor() {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/doctor"
      params={{ orgId, projectId }}
      className="inline-flex w-fit items-center gap-1.5 rounded-sm text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <ArrowLeft aria-hidden className="size-4" strokeWidth={2} />
      Doctor
    </Link>
  );
}

function metaLine(insight: InsightDetail, now: Date): string {
  const parts: string[] = [];
  const first = formatDateTime(insight.first_seen_at);
  if (first !== null) {
    parts.push(`First seen ${first}`);
  }
  const last = compactRelative(insight.last_seen_at, now);
  if (last !== null) {
    parts.push(`last seen ${last}`);
  }
  parts.push(`${insight.occurrences} ${insight.occurrences === 1 ? "occurrence" : "occurrences"}`);
  if (insight.acknowledged_at !== null && insight.status === "acknowledged") {
    const at = formatDateTime(insight.acknowledged_at);
    if (at !== null) {
      parts.push(`Acknowledged ${at}`);
    }
  }
  return parts.join(" · ");
}

interface InsightHeaderProps {
  insight: InsightDetail;
  canManage: boolean;
  now: Date;
}

/**
 * Figma detail header: back link, badges, title, meta line, the status banner, the summary and
 * the actions. The summary leads; the actions sit beside it on a wide screen.
 */
export function InsightHeader({ insight, canManage, now }: InsightHeaderProps) {
  return (
    <div className="flex flex-col gap-4">
      <BackToDoctor />
      <ul aria-label="Severity, status and kind" className="flex flex-wrap items-center gap-2">
        <li>
          <SeverityBadge severity={insight.severity} />
        </li>
        <li>
          <StatusPill status={insight.status} />
        </li>
        <li>
          <KindChip kind={insight.kind} label={insight.label} />
        </li>
      </ul>
      <div className="flex flex-col gap-2">
        <h1 className="text-h1 [overflow-wrap:anywhere] text-foreground">{insight.title}</h1>
        <p className="text-sm text-muted-foreground tabular">{metaLine(insight, now)}</p>
      </div>
      <StatusBanner insight={insight} />
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between lg:gap-8">
        <p className="max-w-3xl text-base leading-relaxed [overflow-wrap:anywhere] text-foreground">
          {insight.summary}
        </p>
        <InsightActions insightId={insight.id} status={insight.status} canManage={canManage} />
      </div>
    </div>
  );
}
