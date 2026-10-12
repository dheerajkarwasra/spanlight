import { Badge } from "@/components/ui/badge";
import type { Score } from "@/lib/api";
import { cn } from "@/lib/utils";

import { sourceLabel } from "./score-format";

const SOURCE_CLASSES: Record<Score["source"], string> = {
  // An evaluator's verdict: the ink pill.
  eval: "",
  // A person: violet outline.
  human: "border border-accent/50",
  // The SDK or the REST API: a neutral outline.
  api: "font-medium text-muted-foreground",
};

const SOURCE_VARIANTS = { eval: "ink", human: "accent", api: "outline" } as const;

/**
 * Figma "Scores/Source badge": `Eval · <evaluator>`, `Human · <author>` or `API`. The score carries
 * its author's name but not its evaluator's, so an evaluator's score reads `Eval` alone.
 */
export function ScoreSourceBadge({ score, className }: { score: Score; className?: string }) {
  const label = sourceLabel(score.source, score.source === "human" ? score.author_name : null);
  return (
    <Badge
      size="sm"
      variant={SOURCE_VARIANTS[score.source]}
      title={label}
      className={cn("max-w-48 min-w-0", SOURCE_CLASSES[score.source], className)}
    >
      <span className="truncate">{label}</span>
    </Badge>
  );
}
