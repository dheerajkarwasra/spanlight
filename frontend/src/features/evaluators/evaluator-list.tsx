import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import { ColumnLabels, TileList } from "@/components/tile-list";
import { useProjectParams } from "@/features/shell";
import type { AlertChannel, Evaluator } from "@/lib/api";
import { cn } from "@/lib/utils";

import { readBudget } from "./budget-math";
import { BudgetMeterInline } from "./budget-meter";
import { KindChip, StatePill } from "./evaluator-chips";
import { evaluatorSummary, samplingLabel } from "./evaluator-labels";

/** Figma "Evaluators — List" column widths, from the container width where columns appear. */
const COLUMNS = {
  kind: "@[52rem]:w-[130px] @[52rem]:shrink-0",
  sampling: "@[52rem]:w-[80px] @[52rem]:shrink-0",
  budget: "min-w-0 @[52rem]:w-[190px] @[52rem]:shrink-0",
  state: "@[52rem]:w-[100px] @[52rem]:shrink-0",
} as const;

interface EvaluatorListProps {
  evaluators: readonly Evaluator[];
  channels: readonly AlertChannel[] | undefined;
}

export function EvaluatorList({ evaluators, channels }: EvaluatorListProps) {
  return (
    <div className="@container flex flex-col gap-1.5">
      <ColumnLabels className="px-5 @[52rem]:flex @[52rem]:gap-4">
        <span className="min-w-0 flex-1">Evaluator</span>
        <span className={COLUMNS.kind}>Kind</span>
        <span className={COLUMNS.sampling}>Sampling</span>
        <span className={COLUMNS.budget}>Monthly budget</span>
        <span className={COLUMNS.state}>State</span>
        <span className="w-4" />
      </ColumnLabels>
      <TileList label="Evaluators">
        {evaluators.map((evaluator) => (
          <EvaluatorRow
            key={evaluator.id}
            evaluator={evaluator}
            channelName={channelName(evaluator, channels)}
          />
        ))}
      </TileList>
    </div>
  );
}

function channelName(evaluator: Evaluator, channels: readonly AlertChannel[] | undefined) {
  const config = evaluator.config;
  if (config.kind !== "webhook") {
    return null;
  }
  return channels?.find((channel) => channel.id === config.channel_id)?.name ?? null;
}

/** "—" for no budget. The row is a link, so the reason is spoken rather than a tooltip. */
function NoBudget({ reason }: { reason: string }) {
  return (
    <span className="text-[13px] font-semibold text-subtle-foreground">
      <span aria-hidden>—</span>
      <span className="sr-only">{reason}</span>
    </span>
  );
}

function BudgetCell({ evaluator }: { evaluator: Evaluator }) {
  if (evaluator.kind !== "llm_judge") {
    return <NoBudget reason="No budget: only LLM judges have one." />;
  }
  const reading = evaluator.budget ? readBudget(evaluator.budget) : null;
  return reading ? (
    <BudgetMeterInline reading={reading} />
  ) : (
    <NoBudget reason="Budget unavailable: this month's spend couldn't be read." />
  );
}

interface EvaluatorRowProps {
  evaluator: Evaluator;
  channelName: string | null;
}

/** Figma "Evaluator row" and its 375 px card: the whole tile opens the evaluator. */
function EvaluatorRow({ evaluator, channelName: channel }: EvaluatorRowProps) {
  const { orgId, projectId } = useProjectParams();
  return (
    <li>
      <Link
        to="/$orgId/$projectId/evaluators/$evaluatorId"
        params={{ orgId, projectId, evaluatorId: evaluator.id }}
        className="flex flex-col gap-3 rounded-tile bg-surface-muted p-4 transition-colors hover:bg-surface-hover @[52rem]:flex-row @[52rem]:items-center @[52rem]:gap-4 @[52rem]:px-5 @[52rem]:py-3.5"
      >
        <div className="flex flex-wrap items-center gap-2 @[52rem]:hidden">
          <StatePill enabled={evaluator.enabled} />
          <KindChip kind={evaluator.kind} />
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <p
            className={cn(
              "truncate text-sm font-semibold",
              evaluator.enabled ? "text-foreground" : "text-muted-foreground",
            )}
          >
            {evaluator.name}
          </p>
          <p className="text-xs font-medium [overflow-wrap:anywhere] text-muted-foreground">
            {evaluatorSummary(evaluator, channel)}
          </p>
        </div>
        <div className={cn("hidden @[52rem]:flex", COLUMNS.kind)}>
          <KindChip kind={evaluator.kind} />
        </div>
        <div className="flex items-center gap-3 @[52rem]:contents">
          <p className={cn("shrink-0 text-[13px] font-semibold text-foreground", COLUMNS.sampling)}>
            <span className="font-medium text-muted-foreground @[52rem]:sr-only">Sampling </span>
            {samplingLabel(evaluator.sampling_rate)}
          </p>
          <div className={cn("min-w-0 flex-1 @[52rem]:flex-none", COLUMNS.budget)}>
            <BudgetCell evaluator={evaluator} />
          </div>
        </div>
        <div className={cn("hidden @[52rem]:flex", COLUMNS.state)}>
          <StatePill enabled={evaluator.enabled} />
        </div>
        <ChevronRight aria-hidden className="hidden size-4 text-muted-foreground @[52rem]:block" />
      </Link>
    </li>
  );
}
