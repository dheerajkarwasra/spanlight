import { ScoreBadge } from "@/components/score-badge";
import { Badge } from "@/components/ui/badge";
import { UnknownValue } from "@/components/unknown-value";
import type { ItemScore as Score } from "@/lib/api";
import { formatScoreValue } from "@/lib/score-format";

import { categoryOrder, itemScoreParts } from "./compare-view";
import { missingScoreReason } from "./item-copy";
import { ScorePill } from "./score-chips";
import type { ScoreMeta } from "./score-meta";

const CATEGORY_BADGE = { success: "success", warning: "warning", danger: "danger" } as const;

interface ItemScoreProps {
  meta: ScoreMeta;
  value: Score | undefined;
  evaluatorError: string | null;
}

/** One item's value of one score: a number pill, Yes / No, or a category; "—" when absent. */
export function ItemScore({ meta, value, evaluatorError }: ItemScoreProps) {
  if (value === undefined) {
    return <UnknownValue reason={missingScoreReason(evaluatorError)} />;
  }
  if (typeof value === "boolean") {
    return <ScoreBadge score={itemScoreParts(value)} dataType="boolean" size="sm" />;
  }
  if (typeof value === "string") {
    // Tone by the configured order (best first); an open list has no order, so no tone.
    const all = Object.fromEntries(meta.categories.map((name) => [name, 1]));
    const known = categoryOrder(meta.categories, [all]).find((item) => item.name === value);
    return (
      <Badge variant={known ? CATEGORY_BADGE[known.tone] : "neutral"}>
        <span className="sr-only">{meta.name}: </span>
        {value}
      </Badge>
    );
  }
  const text = formatScoreValue(itemScoreParts(value), "numeric");
  return <ScorePill label={`${meta.name} ${text}`}>{text}</ScorePill>;
}
