import { Notice } from "@/components/notice";
import type { RunComparison } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { CompareScoreCard } from "./compare-score-card";
import type { SidePair } from "./compare-sides";
import { CompareTable } from "./compare-table";
import { CompareTiles } from "./compare-tiles";
import { changeCounts, costCertainty, formatTradeOff } from "./compare-view";
import type { DatasetItemRef } from "./experiment-queries";
import { leadScore, type ScoreMeta } from "./score-meta";
import { TradeOffCard } from "./trade-off-card";

interface CompareResultProps {
  comparison: RunComparison;
  pair: SidePair;
  /** The scores of both runs, in display order. */
  metas: readonly ScoreMeta[];
  /**
   * `score` is the `?score=` the items sort by (the lead score when unset or unknown); `detail`
   * is "Run #3 against Run #2 · 50 items in both runs".
   */
  sort: { score: string | undefined; onChange: (name: string) => void; detail: string };
  inputs: ReadonlyMap<string, DatasetItemRef> | undefined;
}

/** Everything under the pickers once both runs are chosen: verdict, scores, cost, items. */
export function CompareResult({ comparison, pair, metas, sort, inputs }: CompareResultProps) {
  const lead = leadScore(metas);
  const selected = metas.find((meta) => meta.name === sort.score) ?? lead;
  // The sentence leads with the selected score when it has an average, else the first that has.
  const verdictScore = selected && selected.kind !== "categorical" ? selected : lead;
  const sentence = formatTradeOff({
    score: verdictScore && verdictScore.kind !== "categorical" ? verdictScore.name : null,
    kind: verdictScore?.kind ?? "numeric",
    scoreDelta: verdictScore ? (comparison.aggregate[verdictScore.name]?.delta ?? null) : null,
    costDelta: comparison.cost_delta_usd,
    costCertainty: costCertainty(
      pair.a.totals?.has_unpriced ?? false,
      pair.b.totals?.has_unpriced ?? false,
    ),
    latencyP95Delta: comparison.latency_p95_delta_ms,
  });
  const scored = verdictScore ? changeCounts(comparison.items, verdictScore.name).scoredBoth : null;

  return (
    <>
      <TradeOffCard
        sentence={sentence}
        detail={
          scored === null
            ? sort.detail
            : `${sort.detail} · ${formatInteger(scored)} scored on both sides`
        }
      />
      <section aria-labelledby="compare-scores" className="flex flex-col gap-4">
        <h2 id="compare-scores" className="sr-only">
          Scores
        </h2>
        {metas.length > 0 ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {metas.map((meta) => (
              <CompareScoreCard
                key={meta.name}
                meta={meta}
                delta={comparison.aggregate[meta.name]}
                counts={changeCounts(comparison.items, meta.name)}
                sides={pair}
              />
            ))}
          </div>
        ) : (
          <Notice>
            Neither run has scores, so only cost, latency and errors are compared. Attach evaluators
            to a new experiment to score its items.
          </Notice>
        )}
      </section>
      <section aria-labelledby="compare-cost" className="flex flex-col">
        <h2 id="compare-cost" className="sr-only">
          Cost, latency and errors
        </h2>
        <CompareTiles pair={pair} comparison={comparison} />
      </section>
      {selected ? (
        <CompareTable
          items={comparison.items}
          sort={{ scores: metas, score: selected, onChange: sort.onChange }}
          inputs={inputs}
          names={{ a: pair.a.name, b: pair.b.name }}
        />
      ) : null}
    </>
  );
}
