import { SectionCard } from "@/components/section-card";
import { UnknownValue } from "@/components/unknown-value";
import type { RunTotals, ScoreStat } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { categoryOrder, formatMean } from "./compare-view";
import { DistributionBar, DistributionLegend, type Segment } from "./distribution-bar";
import { KindChip } from "./score-chips";
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
      description="Mean per score across the items that were scored. Higher is better."
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
  return (
    <li className="flex flex-col gap-3 rounded-tile bg-surface-muted p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[15px] font-bold text-foreground">{meta.name}</span>
        {meta.evaluatorKind ? <KindChip kind={meta.evaluatorKind} /> : null}
      </div>
      <p className="text-[32px] leading-none font-extrabold tracking-[-0.04em] text-foreground tabular">
        {content.value ?? <UnknownValue reason="No item of this run has this score." />}
      </p>
      <p className="text-xs font-medium text-muted-foreground">{content.note}</p>
      {content.segments ? <DistributionBar segments={content.segments} /> : null}
      {content.segments ? <DistributionLegend segments={content.segments} /> : null}
    </li>
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
