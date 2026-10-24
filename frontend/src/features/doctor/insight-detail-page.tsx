import { getRouteApi } from "@tanstack/react-router";

import { usePermission } from "@/features/shell";
import type { InsightDetail } from "@/lib/api";
import { useNow } from "@/lib/use-now";

import { useIncidentPolling, useInsightQuery } from "./doctor-queries";
import { EvidenceCard } from "./evidence-card";
import { EvidenceTraceLinks } from "./evidence-trace-links";
import { ExplainSection } from "./explain-section";
import { FeedbackRow } from "./feedback-row";
import { FixAndVerification } from "./fix-and-verification";
import { incidentCitation, showsPollingOffNote } from "./incident-citation";
import { IncidentLine, PollingOffLine } from "./incident-line";
import { InsightDetailSkeleton, InsightLoadError } from "./insight-detail-states";
import { InsightHeader } from "./insight-header";
import { CertaintyCard, LayerCard } from "./layer-and-certainty";
import { SetPriceLink } from "./set-price-link";
import { TrackRecordLine } from "./track-record-line";

const insightRoute = getRouteApi("/_authed/$orgId/$projectId/doctor/$insightId");

/**
 * Figma "Doctor finding": header and summary, the evidence (chart first), the example traces
 * opening on their span, the check's track record, the fix and how to verify it, where it fails,
 * how certain the finding is, the vote, then Explain (collapsed).
 */
export function InsightDetailPage() {
  const { insightId } = insightRoute.useParams();
  const query = useInsightQuery(insightId);

  if (query.isPending) {
    return <InsightDetailSkeleton />;
  }
  // A failed background refresh keeps the loaded insight on screen; polling goes on.
  if (query.data === undefined) {
    return (
      <InsightLoadError
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  return <LoadedInsight insight={query.data} />;
}

function LoadedInsight({ insight }: { insight: InsightDetail }) {
  const canManage = usePermission("insights:manage");
  const canVote = usePermission("insights:feedback");
  const now = useNow();
  return (
    <div className="flex flex-col gap-6">
      <InsightHeader insight={insight} canManage={canManage} now={now} />
      <EvidenceCard insight={insight}>
        <IncidentEvidence insight={insight} />
      </EvidenceCard>
      <EvidenceTraceLinks traceIds={insight.evidence.trace_ids} spans={insight.evidence_spans} />
      <TrackRecordLine kind={insight.kind} />
      <FixAndVerification
        fix={insight.suggested_fix}
        verification={insight.verification}
        fixAction={
          insight.kind === "unpriced_spend" ? <SetPriceLink insight={insight} /> : undefined
        }
      />
      <div className="grid gap-5 md:grid-cols-2">
        <LayerCard layer={insight.failure_layer} />
        <CertaintyCard certainty={insight.certainty} />
      </div>
      <FeedbackRow
        insightId={insight.id}
        feedback={insight.feedback}
        canVote={canVote}
        canManage={canManage}
      />
      <ExplainSection
        insightId={insight.id}
        explanations={insight.explanations}
        canManage={canManage}
      />
    </div>
  );
}

/**
 * Figma "Doctor/Insight detail/Incident line": the matched status-page incident, or, for a
 * `provider_incident` finding without one, why none could be matched when polling is off.
 */
function IncidentEvidence({ insight }: { insight: InsightDetail }) {
  const metrics = insight.evidence.metrics;
  const citation = incidentCitation(metrics);
  const unmatched = insight.kind === "provider_incident" && citation === null;
  const polling = useIncidentPolling(unmatched);
  if (citation !== null) {
    return <IncidentLine citation={citation} />;
  }
  return showsPollingOffNote(insight.kind, metrics, polling.data) ? <PollingOffLine /> : null;
}
