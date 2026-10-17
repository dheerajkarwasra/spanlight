import { Callout } from "@/components/callout";
import { Button } from "@/components/ui/button";
import type { BillingSource, ReconciliationReport } from "@/lib/api";

import { failingCopy, neverFetchedCopy } from "./reconciliation-copy";
import { pendingDayRanges } from "./reconciliation-table";
import { RotateSourceDialog } from "./rotate-source-dialog";

interface SourceCalloutProps {
  source: BillingSource;
  /** The report, once loaded: it names the days a failing fetch leaves open. */
  report: ReconciliationReport | undefined;
  canManage: boolean;
}

/**
 * Figma "Callout · Never fetched" (info) and "Callout · Fetch failing" (danger, with Rotate key
 * when the provider refused the admin key). Nothing for a source whose last fetch worked.
 */
export function SourceCallout({ source, report, canManage }: SourceCalloutProps) {
  if (source.status === "never_fetched") {
    const copy = neverFetchedCopy(source);
    return (
      <Callout tone="info" title={copy.title}>
        {copy.body}
      </Callout>
    );
  }
  if (source.status !== "failing") {
    return null;
  }
  const ranges = report ? pendingDayRanges(report) : { unfetched: null, provisional: null };
  const copy = failingCopy(source, ranges);
  const action =
    copy.rotate && canManage ? (
      <RotateSourceDialog
        source={source}
        trigger={
          <Button size="sm" variant="secondary" className="shadow-none">
            Rotate key
          </Button>
        }
      />
    ) : undefined;
  return (
    <Callout tone="danger" title={copy.title} action={action}>
      {copy.body}
    </Callout>
  );
}
