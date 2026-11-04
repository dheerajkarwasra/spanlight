import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight, Info, TriangleAlert } from "lucide-react";

import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import { ingestQuotaApi, queryKeys, type CompletenessQuery } from "@/lib/api";
import { useTapTooltip } from "@/lib/use-tap-tooltip";
import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { REFUSED_TOOLTIP, refusedSentences } from "./refused-spans-copy";

/** The project's ingest quota over the page's window (losses are per project, not environment). */
function useIngestQuotaQuery() {
  const { projectId } = useProjectParams();
  const { range } = useProjectFilters();
  const query: CompletenessQuery = { from: range.from, to: range.to };
  return useQuery({
    queryKey: queryKeys.project(projectId).ingestQuota(query),
    queryFn: ({ signal }) => ingestQuotaApi.get(projectId, query, signal),
  });
}

/**
 * Figma "Overview/Refused spans notice": shown above the KPI ribbon and the traces list only when
 * the window has spans the server refused for the project's caps. It says "refused", never
 * "lost", and does not reuse the aggregate chips' words (complete, pending, unavailable): a window
 * can be complete and still carry refusals. Nothing renders while the quota loads or fails: the
 * absence of a notice is not a claim, the completeness card carries the counts. `role="status"`:
 * it appears after the load, so assistive tech is told.
 */
export function RefusedSpansNotice({ className }: { className?: string }) {
  const { orgId, projectId } = useProjectParams();
  const quota = useIngestQuotaQuery();
  const sentences = quota.data ? refusedSentences(quota.data.rejected_days) : [];
  if (sentences.length === 0) {
    return null;
  }
  return (
    <div
      role="status"
      className={cn(
        "flex items-start gap-3 rounded-tile bg-warning-subtle px-4 py-3.5 text-xs leading-[1.4] font-medium text-warning",
        className,
      )}
    >
      <TriangleAlert aria-hidden className="mt-px size-4 shrink-0" strokeWidth={2} />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex items-start gap-1.5">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            {sentences.map((sentence) => (
              <p key={sentence}>{sentence}</p>
            ))}
          </div>
          <RefusedInfo />
        </div>
        <Link
          to="/$orgId/$projectId/settings/project"
          params={{ orgId, projectId }}
          hash="ingest-quota"
          className="inline-flex min-h-11 w-fit items-center gap-0.5 rounded-sm text-xs font-semibold text-accent hover:underline"
        >
          Ingest quota settings
          <ChevronRight aria-hidden className="size-3.5" />
        </Link>
      </div>
    </div>
  );
}

function RefusedInfo() {
  const { rootProps, triggerProps } = useTapTooltip();
  return (
    <TooltipRoot {...rootProps}>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label="What counts as refused"
          {...triggerProps}
          className="relative inline-flex size-4 shrink-0 items-center justify-center rounded-full after:absolute after:-inset-3.5"
        >
          <Info aria-hidden className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent>{REFUSED_TOOLTIP}</TooltipContent>
    </TooltipRoot>
  );
}
