import { ArrowDown, ArrowRight, ArrowUp, Minus } from "lucide-react";
import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { UnknownValue } from "@/components/unknown-value";
import type { ScoreDelta, ScoreStat } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  categoryOrder,
  deltaTone,
  formatMean,
  formatScoreDelta,
  testSentence,
  type ChangeCounts,
} from "./compare-view";
import { sideReason, type RunSide, type SidePair } from "./compare-sides";
import { DistributionBar, DistributionLegend } from "./distribution-bar";
import { ChangePill, JudgeAgreement, KindChip } from "./score-chips";
import type { ScoreMeta } from "./score-meta";

interface CompareScoreCardProps {
  meta: ScoreMeta;
  delta: ScoreDelta | undefined;
  counts: ChangeCounts;
  /** The two runs: their names, and their totals for a categorical score's counts. */
  sides: SidePair;
}

const NO_MEAN = "Unknown: no item of this run has this score.";

function Values({ a, b, sides }: { a: ReactNode; b: ReactNode; sides: SidePair }) {
  return (
    <div className="flex flex-wrap items-center gap-3.5">
      <div className="flex flex-col text-muted-foreground">
        <span className="text-xs font-medium">A · {sides.a.name}</span>
        <span className="text-[28px] leading-tight font-bold tracking-[-0.03em] tabular">{a}</span>
      </div>
      <ArrowRight aria-hidden className="size-[18px] text-muted-foreground" />
      <div className="flex flex-col">
        <span className="text-xs font-medium text-muted-foreground">B · {sides.b.name}</span>
        <span className="text-[28px] leading-tight font-extrabold tracking-[-0.03em] text-foreground tabular">
          {b}
        </span>
      </div>
    </div>
  );
}

function Count({ tone, children }: { tone: "good" | "bad" | "neutral"; children: ReactNode }) {
  const Icon = tone === "good" ? ArrowUp : tone === "bad" ? ArrowDown : Minus;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold",
        tone === "good" && "bg-success-subtle text-success",
        tone === "bad" && "bg-danger-subtle text-danger-text",
        tone === "neutral" && "border border-border-strong bg-surface text-muted-foreground",
      )}
    >
      <Icon aria-hidden className="size-3" strokeWidth={2.5} />
      {children}
    </span>
  );
}

/** One score of Figma "Experiments — Compare": A → B, the change and how items moved. */
export function CompareScoreCard(props: CompareScoreCardProps) {
  const { meta } = props;
  return (
    <Card className="flex min-w-0 flex-col gap-3 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[15px] font-bold text-foreground">{meta.name}</h3>
        {meta.evaluatorKind ? <KindChip kind={meta.evaluatorKind} /> : null}
        <span className="ml-auto">
          <JudgeAgreement scoreName={meta.name} judge={meta.evaluatorKind === "llm_judge"} />
        </span>
      </div>
      {meta.kind === "categorical" ? <Categorical {...props} /> : <Numeric {...props} />}
    </Card>
  );
}

function Numeric({ meta, delta, counts, sides }: CompareScoreCardProps) {
  const mean = (value: number | null | undefined) =>
    formatMean(value ?? null, meta.kind) ?? <UnknownValue reason={NO_MEAN} />;
  const change = delta?.delta ?? null;
  const tone = deltaTone(change, true);
  const improved = delta?.improved ?? 0;
  const regressed = delta?.regressed ?? 0;
  const unchanged = delta?.unchanged ?? 0;
  const sentence = delta ? testSentence(delta, meta.kind, counts.scoredBoth) : null;
  return (
    <>
      <Values a={mean(delta?.mean_a)} b={mean(delta?.mean_b)} sides={sides} />
      <div className="flex items-center gap-2">
        {tone === "neutral" ? null : (
          <ChangePill change={tone === "good" ? "improved" : "regressed"} />
        )}
        <span
          className={cn(
            "text-sm font-extrabold tabular",
            tone === "good" && "text-success",
            tone === "bad" && "text-danger-text",
            tone === "neutral" && "text-muted-foreground",
          )}
        >
          {formatScoreDelta(change, meta.kind) ?? (
            <UnknownValue reason="Unknown: one run has no mean." />
          )}
        </span>
      </div>
      {/* Neutral on purpose: significance says the runs differ, not which one is better. */}
      {sentence ? (
        <p className="text-sm font-medium text-muted-foreground">{sentence.text}</p>
      ) : null}
      <div className="flex flex-wrap gap-1.5">
        <Count tone="good">{formatInteger(improved)} improved</Count>
        <Count tone="bad">{formatInteger(regressed)} regressed</Count>
        <Count tone="neutral">{formatInteger(unchanged)} unchanged</Count>
      </div>
      <DistributionBar
        segments={[
          { label: "improved", count: improved, tone: "success" },
          { label: "regressed", count: regressed, tone: "danger" },
          { label: "unchanged", count: unchanged, tone: "neutral" },
        ]}
      />
      <p className="text-xs font-medium text-muted-foreground">
        {formatInteger(counts.scoredBoth)} scored on both sides ·{" "}
        {formatInteger(counts.notComparable)} not comparable
      </p>
    </>
  );
}

function Categorical({ meta, counts, sides }: CompareScoreCardProps) {
  const statA = sides.a.totals?.scores[meta.name];
  const statB = sides.b.totals?.scores[meta.name];
  const order = categoryOrder(meta.categories, [statA?.categories, statB?.categories]);
  const segmentsOf = (stat: ScoreStat | undefined) =>
    order.map(({ name, tone }) => ({ label: name, count: stat?.categories?.[name] ?? 0, tone }));
  const top = order[0]?.name;
  const headline = (side: RunSide, stat: ScoreStat | undefined) =>
    top === undefined || side.totals === null ? (
      <UnknownValue reason={sideReason(side, NO_MEAN)} />
    ) : (
      `${formatInteger(stat?.categories?.[top] ?? 0)} ${top}`
    );
  return (
    <>
      <Values a={headline(sides.a, statA)} b={headline(sides.b, statB)} sides={sides} />
      <p className="text-xs font-medium text-muted-foreground">
        Categories are compared, not ranked: {formatInteger(counts.changed)} items changed,{" "}
        {formatInteger(counts.unchanged)} stayed the same.
      </p>
      {[
        { name: `A · ${sides.a.name}`, stat: statA },
        { name: `B · ${sides.b.name}`, stat: statB },
      ].map(({ name, stat }) => (
        <div key={name} className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold text-muted-foreground">{name}</span>
          <DistributionBar segments={segmentsOf(stat)} />
          <DistributionLegend segments={segmentsOf(stat)} />
        </div>
      ))}
    </>
  );
}
