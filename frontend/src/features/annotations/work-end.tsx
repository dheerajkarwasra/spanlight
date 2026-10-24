import { Link } from "@tanstack/react-router";
import { Check, ClipboardCheck } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useProjectParams } from "@/features/shell";
import type { QueueProgress } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";

import { KeyChip } from "./key-chip";

interface DoneCardProps {
  queueId: string;
  progress: QueueProgress | null;
  /** Scores per finished item: every score of the queue is required. */
  scoresPerItem: number;
  /** The "Last item" strip for the final item, when it was submitted. */
  lastItem: ReactNode;
}

/** Figma "Annotate — all done": the claim found nothing left to review. */
export function AllDoneCard({ queueId, progress, scoresPerItem, lastItem }: DoneCardProps) {
  const { orgId, projectId } = useProjectParams();
  return (
    <EndCard
      icon={<Check aria-hidden className="size-5 text-success" strokeWidth={2.25} />}
      iconClass="border border-success bg-success-subtle"
      title="All done"
      description="Nothing left to review in this queue."
    >
      {progress === null ? null : (
        <dl className="grid w-full max-w-[400px] grid-cols-3 gap-2">
          <Stat label="Reviewed" value={progress.done} />
          <Stat label="Skipped" value={progress.skipped} />
          <Stat label="Scores written" value={progress.done * scoresPerItem} />
        </dl>
      )}
      {lastItem === null ? null : <div className="w-full max-w-[400px] text-left">{lastItem}</div>}
      <Button variant="primary" asChild>
        <Link to="/$orgId/$projectId/queues/$queueId" params={{ orgId, projectId, queueId }}>
          Back to queue
        </Link>
      </Button>
    </EndCard>
  );
}

interface ReleasedCardProps {
  pending: number | null;
  claiming: boolean;
  onClaim: () => void;
}

/** Figma "Annotate — no item claimed": the reviewer released their item. */
export function ReleasedCard({ pending, claiming, onClaim }: ReleasedCardProps) {
  const waiting = pending === null ? "" : `; ${pluralize(pending, "item")} waiting`;
  return (
    <EndCard
      icon={<ClipboardCheck aria-hidden className="size-5 text-accent" />}
      iconClass="bg-accent-subtle"
      title="No item claimed"
      description={`You released the last item. Claim the next one to keep scoring${waiting}.`}
    >
      <div className="flex items-center gap-2.5">
        <Button variant="primary" loading={claiming} aria-keyshortcuts="N" onClick={onClaim}>
          Claim next item
        </Button>
        <KeyChip>N</KeyChip>
      </div>
    </EndCard>
  );
}

interface EndCardProps {
  icon: ReactNode;
  iconClass: string;
  title: string;
  description: string;
  children: ReactNode;
}

function EndCard({ icon, iconClass, title, description, children }: EndCardProps) {
  return (
    <Card className="flex flex-col items-center gap-4 px-6 py-10 text-center sm:py-12">
      <span className={`flex size-[46px] items-center justify-center rounded-full ${iconClass}`}>
        {icon}
      </span>
      <h2 className="text-2xl font-bold tracking-[-0.02em] text-foreground">{title}</h2>
      <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      {children}
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col items-start gap-1 rounded-tile bg-surface-muted px-4 py-3">
      <dt className="text-2xs font-medium text-muted-foreground">{label}</dt>
      <dd className="text-xl font-bold text-foreground tabular">
        {formatInteger(value) ?? String(value)}
      </dd>
    </div>
  );
}
