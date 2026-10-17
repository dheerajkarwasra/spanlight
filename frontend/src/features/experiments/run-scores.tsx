import { SectionCard } from "@/components/section-card";
import { UnknownValue } from "@/components/unknown-value";
import type { RunTotals, ScoreStat } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { categoryOrder, FEW_ITEMS, formatInterval, formatMean } from "./compare-view";
import { DistributionBar, DistributionLegend, type Segment } from "./distribution-bar";
import { FewItemsChip, KindChip } from "./score-chips";
import type { ScoreMeta } from "./score-meta";

interface RunScoresProps {
  totals: RunTotals;
  scores: readonly ScoreMeta[];
}

/** Figma "Experiments — Run" Scores card: the mean (or the categories) of each score. */
export function RunScores({ totals, scores }: RunScoresProps) {
  if (scores.length === 0) {
    return null;
  }
  return (
    <SectionCard
      title="Scores"
      description="Mean per score across the items that were scored. Higher is better. Intervals are shown for yes/no scores only."
    >
      <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {scores.map((meta) => (
          <ScoreTile
            key={meta.name}
            meta={meta}
            stat={totals.scores[meta.name]}
            items={totals.items}
          />
        ))}
      </ul>
    </SectionCard>
  );
}

function ScoreTile({
  meta,
  stat,
  items,
}: {
  meta: ScoreMeta;
  stat: ScoreStat | undefined;
  items: number;
}) {
  const content = describe(meta, stat, items);
  const interval = meta.kind === "boolean" && content.value !== null ? stat : undefined;
  return (
    <li className="flex flex-col gap-3 rounded-tile bg-surface-muted p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[15px] font-bold text-foreground">{meta.name}</span>
        {meta.evaluatorKind ? <KindChip kind={meta.evaluatorKind} /> : null}
      </div>
      <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[32px] leading-none font-extrabold tracking-[-0.04em] text-foreground tabular">
        {content.value ?? <UnknownValue reason="No item of this run has this score." />}
        {interval ? <IntervalText stat={interval} /> : null}
      </p>
      {interval ? <IntervalBar stat={interval} /> : null}
      <p className="text-xs font-medium text-muted-foreground">{content.note}</p>
      {content.segments ? <DistributionBar segments={content.segments} /> : null}
      {content.segments ? <DistributionLegend segments={content.segments} /> : null}
    </li>
  );
}

/** "(95% CI 64–88%, n = 50)", always in words; the bar below only repeats it. */
function IntervalText({ stat }: { stat: ScoreStat }) {
  const text = formatInterval(stat.ci_low, stat.ci_high, stat.count);
  return (
    <>
      <span className="text-sm font-medium tracking-normal text-muted-foreground">
        (
        {text ?? (
          <>
            95% CI{" "}
            <UnknownValue reason="Unknown: these totals were stored before intervals existed." />
          </>
        )}
        )
      </span>
      {stat.count < FEW_ITEMS ? <FewItemsChip /> : null}
    </>
  );
}

const percent = (value: number) => `${Math.min(100, Math.max(0, value * 100))}%`;

/** The interval as a thin range on 0–100% with a tick at the mean. Decorative: the text says it. */
function IntervalBar({ stat: { ci_low, ci_high, mean } }: { stat: ScoreStat }) {
  if (ci_low === null || ci_high === null || mean === null) {
    return null;
  }
  const from = Number(ci_low);
  return (
    <div aria-hidden className="relative h-1.5 w-full rounded-full bg-border">
      <span
        className="absolute inset-y-0 rounded-full bg-muted-foreground"
        style={{ left: percent(from), width: percent(Number(ci_high) - from) }}
      />
      <span
        className="absolute -inset-y-0.5 w-0.5 -translate-x-1/2 rounded-full bg-foreground"
        style={{ left: percent(mean) }}
      />
    </div>
  );
}

function describe(
  meta: ScoreMeta,
  stat: ScoreStat | undefined,
  items: number,
): { value: string | null; note: string; segments?: Segment[] } {
  const count = stat?.count ?? 0;
  const without = Math.max(0, items - count);
  if (meta.kind === "categorical") {
    const order = categoryOrder(meta.categories, [stat?.categories]);
    const segments = order.map(({ name, tone }) => ({
      label: name,
      count: stat?.categories?.[name] ?? 0,
      tone,
    }));
    const top = segments[0];
    return {
      value: top ? `${formatInteger(top.count)} ${top.label}` : null,
      note: `${formatInteger(count)} scored`,
      segments,
    };
  }
  if (meta.kind === "boolean" && stat?.mean !== null && stat?.mean !== undefined) {
    const yes = Math.round(stat.mean * count);
    return {
      value: formatMean(stat.mean, "boolean"),
      note: `${formatInteger(yes)} yes · ${formatInteger(count - yes)} no`,
    };
  }
  return {
    value: formatMean(stat?.mean ?? null, meta.kind),
    note: `${formatInteger(count)} scored · ${formatInteger(without)} without a score`,
  };
}
