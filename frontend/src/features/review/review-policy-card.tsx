import { ClipboardCheck } from "lucide-react";
import { useId, useState } from "react";

import { DisabledReason } from "@/components/disabled-reason";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission } from "@/features/shell";
import type { AnnotationQueue, ScoreConfig } from "@/lib/api";
import { roleBlockedCopy } from "@/lib/permissions";

import { PolicyDetails } from "./policy-details";
import { useReviewPolicyQuery } from "./review-queries";
import { SetupReviewDialog } from "./setup-review-dialog";

interface ReviewPolicyCardProps {
  /** The project's queues (the page's first page), to pick from in set-up. */
  queues: readonly AnnotationQueue[] | undefined;
  configs: readonly ScoreConfig[] | undefined;
}

/**
 * Figma "Review policy card" (empty, enabled, paused, loading, error) above the queue list: the
 * daily review that fills a queue every UTC night. Everyone reads it; members set it up.
 */
export function ReviewPolicyCard({ queues, configs }: ReviewPolicyCardProps) {
  const query = useReviewPolicyQuery();
  const canWrite = usePermission("annotations:write");
  // Owned here, not by the empty state: set-up success swaps the empty state for the details.
  const [setupOpen, setSetupOpen] = useState(false);

  let body;
  if (query.isPending) {
    body = (
      <Skeleton role="status" aria-label="Loading the daily review" className="h-40 rounded-card" />
    );
  } else if (query.data === undefined) {
    body = (
      <SectionCard title="Daily review">
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load the daily review"
          retrying={query.isFetching}
          onRetry={() => {
            void query.refetch();
          }}
        />
      </SectionCard>
    );
  } else if (query.data === null) {
    body = <EmptyPolicy canWrite={canWrite} onSetup={() => setSetupOpen(true)} />;
  } else {
    body = <PolicyDetails policy={query.data} canWrite={canWrite} />;
  }

  return (
    <>
      {body}
      <SetupReviewDialog
        open={setupOpen}
        onOpenChange={setSetupOpen}
        queues={queues}
        configs={configs}
      />
    </>
  );
}

function EmptyPolicy({ canWrite, onSetup }: { canWrite: boolean; onSetup: () => void }) {
  const titleId = useId();
  const button = (
    <Button variant="primary" disabled={!canWrite} onClick={onSetup}>
      Set up daily review
    </Button>
  );
  return (
    <Card
      role="region"
      aria-labelledby={titleId}
      className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:gap-5 sm:p-6"
    >
      <span
        aria-hidden
        className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-accent-subtle text-accent"
      >
        <ClipboardCheck className="size-5" />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <CardTitle id={titleId}>Daily review</CardTitle>
        <p className="text-sm text-muted-foreground">
          No daily review yet. Spanlight can pick the traces worth reading every day: errors, cost
          outliers, open Doctor findings and a random share.
        </p>
      </div>
      <div className="shrink-0">
        {canWrite ? (
          button
        ) : (
          <DisabledReason reason={roleBlockedCopy("set up the daily review")}>
            {button}
          </DisabledReason>
        )}
      </div>
    </Card>
  );
}
