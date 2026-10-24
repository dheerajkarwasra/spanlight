import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import { badgeVariants } from "@/components/ui/badge";
import { SEVERITY_LABELS, SEVERITY_STYLE } from "@/features/doctor";
import { useProjectParams } from "@/features/shell/project-context";
import { cn } from "@/lib/utils";

import { useTraceInsights } from "./trace-queries";

/**
 * Figma "Doctor/Insight badge": "Critical · Retry storm ›", one chip per insight. The chip is its
 * own shape (a link with a chevron); the severity icon and wording are the Doctor's, so severity
 * never reads by colour alone. `compact` (the summary bar) keeps one row and lets the chips
 * shrink, with the severity word left to the icon and screen readers.
 */
export function InsightBadges({
  traceId,
  compact = false,
}: {
  traceId: string;
  compact?: boolean;
}) {
  const { orgId, projectId } = useProjectParams();
  const insights = useTraceInsights(traceId);
  if (insights.length === 0) {
    return null;
  }
  return (
    <ul
      aria-label="Doctor findings for this trace"
      className={cn("flex items-center gap-2", compact ? "min-w-0 overflow-hidden" : "flex-wrap")}
    >
      {insights.map((insight) => {
        const { variant, icon: Icon } = SEVERITY_STYLE[insight.severity];
        return (
          <li key={insight.id} className="min-w-0">
            <Link
              to="/$orgId/$projectId/doctor/$insightId"
              params={{ orgId, projectId, insightId: insight.id }}
              title={insight.title}
              className={cn(
                badgeVariants({ variant }),
                "max-w-full border border-current/25 hover:underline",
              )}
            >
              <Icon aria-hidden strokeWidth={2} />
              <span className="truncate">
                <span className={cn(compact && "sr-only")}>
                  {SEVERITY_LABELS[insight.severity]} ·{" "}
                </span>
                {insight.label}
              </span>
              <ChevronRight aria-hidden />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
