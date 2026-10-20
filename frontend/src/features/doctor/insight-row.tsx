import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import { useProjectParams } from "@/features/shell";
import type { Insight } from "@/lib/api";
import { cn } from "@/lib/utils";

import { activityLine, formatDateTime, LAYER_LABELS } from "./insight-format";
import { KindChip } from "./kind-chip";
import { SeverityBadge } from "./severity-badge";

interface InsightRowProps {
  insight: Insight;
  now: Date;
}

/** The extra line a muted or resolved insight carries under its activity. */
function stateLine(insight: Insight): string | null {
  if (insight.status === "muted") {
    const until = formatDateTime(insight.muted_until);
    return until === null ? "Muted" : `Muted until ${until}`;
  }
  if (insight.status === "resolved") {
    const at = formatDateTime(insight.resolved_at);
    return at === null ? "Resolved" : `Resolved ${at}`;
  }
  return null;
}

/**
 * Figma "Doctor/Insight row": severity, title with kind and layer, activity, chevron. The whole
 * tile opens the insight; the link's name is the title. Critical rows are tinted.
 */
export function InsightRow({ insight, now }: InsightRowProps) {
  const { orgId, projectId } = useProjectParams();
  const extra = stateLine(insight);

  return (
    <li
      className={cn(
        "relative flex flex-col gap-2.5 rounded-tile p-4 transition-colors",
        "@[44rem]:min-h-[82px] @[44rem]:flex-row @[44rem]:items-center @[44rem]:gap-5 @[44rem]:px-5 @[44rem]:py-3.5",
        insight.severity === "critical"
          ? "bg-danger-subtle hover:bg-danger-subtle/80"
          : "bg-surface-muted hover:bg-surface-hover",
      )}
    >
      <div className="@[44rem]:w-[110px] @[44rem]:shrink-0">
        <SeverityBadge severity={insight.severity} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <Link
          to="/$orgId/$projectId/doctor/$insightId"
          params={{ orgId, projectId, insightId: insight.id }}
          className="text-sm font-semibold [overflow-wrap:anywhere] text-foreground outline-none after:absolute after:inset-0 after:rounded-tile focus-visible:after:outline-[1.5px] focus-visible:after:outline-offset-2 focus-visible:after:outline-ring"
        >
          {insight.title}
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <KindChip kind={insight.kind} label={insight.label} />
          <span className="text-xs font-medium text-muted-foreground">
            {LAYER_LABELS[insight.failure_layer]} layer
          </span>
        </div>
      </div>
      <div className="flex flex-col gap-0.5 text-xs font-medium text-muted-foreground @[44rem]:w-[250px] @[44rem]:shrink-0">
        <span className="tabular">{activityLine(insight, now)}</span>
        {extra === null ? null : <span>{extra}</span>}
      </div>
      <ChevronRight aria-hidden className="hidden size-4 shrink-0 text-foreground @[44rem]:block" />
    </li>
  );
}
