import { UnknownValue } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import type { ScoreDataType, ScoreSummary } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatScoreValue } from "@/lib/score-format";

interface ScoresCellProps {
  /** The trace's latest score of each name, most recently scored first. */
  scores: readonly ScoreSummary[];
  /**
   * A score name's type, so a boolean reads Yes/No: null when the name has no config, undefined
   * while the configs load.
   */
  typeOf: (name: string) => ScoreDataType | null | undefined;
  /** How many chips to show before the rest collapse into "+N". */
  max?: number;
}

function summaryText(score: ScoreSummary, typeOf: ScoresCellProps["typeOf"]): string {
  return `${score.name} ${formatScoreValue(score, typeOf(score.name) ?? null)}`;
}

/**
 * The explorer's Scores column (Figma "Scores/Score chip"): the most recently scored name with
 * its latest value, then "+N" for the others, listed in a tooltip.
 */
export function ScoresCell({ scores, typeOf, max = 1 }: ScoresCellProps) {
  if (scores.length === 0) {
    return <UnknownValue reason="No scores on this trace." />;
  }
  const visible = scores.slice(0, max);
  const hidden = scores.slice(max);
  // A 1 or 0 may be a boolean's Yes or No: wait for the configs rather than show the number.
  const waiting = scores.some(
    (score) => score.string_value === null && typeOf(score.name) === undefined,
  );
  if (waiting) {
    return <Skeleton aria-hidden className="h-5 w-24 rounded-full" />;
  }
  const hiddenText = hidden.map((score) => summaryText(score, typeOf)).join(", ");

  return (
    <span className="flex min-w-0 items-center gap-1">
      {visible.map((score) => (
        <ScoreChip
          key={score.name}
          score={score}
          value={formatScoreValue(score, typeOf(score.name) ?? null)}
        />
      ))}
      {hidden.length > 0 ? (
        <Tooltip content={hiddenText}>
          <Badge
            variant="outline"
            size="sm"
            tabIndex={0}
            className="relative cursor-help after:absolute after:-inset-0.5"
            aria-label={`${String(hidden.length)} more: ${hiddenText}`}
          >
            +{hidden.length}
          </Badge>
        </Tooltip>
      ) : null}
    </span>
  );
}

/** "relevance 0.82": the name in muted text, the value in mono. */
export function ScoreChip({
  score,
  value,
  className,
}: {
  score: ScoreSummary;
  value: string;
  className?: string;
}) {
  return (
    <span
      title={`${score.name}: ${value}`}
      className={cn(
        "inline-flex min-w-0 items-center gap-1.5 rounded-full border border-border-strong bg-surface px-2 py-0.5 text-2xs",
        className,
      )}
    >
      <span className="min-w-0 truncate font-medium text-muted-foreground">{score.name}</span>
      <span className="shrink-0 font-mono font-semibold text-foreground tabular">{value}</span>
    </span>
  );
}
