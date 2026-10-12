import { Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { RelativeTime } from "@/components/relative-time";
import { ScoreBadge } from "@/components/score-badge";
import { Button } from "@/components/ui/button";
import type { Score } from "@/lib/api";
import { formatScoreValue } from "@/lib/score-format";

import { scoreErrorMessage } from "./score-errors";
import { ScoreSourceBadge } from "./score-source-badge";
import { useDeleteScore } from "./scores-queries";

interface ScoreRowProps {
  score: Score;
  /** The author of a human score, or an admin for any score. The server checks again. */
  canDelete: boolean;
}

/**
 * Figma "Trace detail" score tile: the value, where it came from and when, a delete action, and
 * the comment underneath.
 */
export function ScoreRow({ score, canDelete }: ScoreRowProps) {
  const remove = useDeleteScore(score.trace_id);
  const value = formatScoreValue(score, score.data_type);

  return (
    <li className="flex flex-col gap-2 rounded-tile bg-surface-muted px-3.5 py-3">
      <div className="flex min-w-0 items-center gap-2.5">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <ScoreBadge score={score} dataType={score.data_type} />
          <ScoreSourceBadge score={score} />
          <RelativeTime
            iso={score.created_at}
            className="basis-full text-xs font-medium text-muted-foreground sm:basis-auto"
          />
        </div>
        {canDelete ? (
          <ConfirmDialog
            trigger={
              <Button
                variant="secondary"
                size="icon-sm"
                aria-label={`Delete ${score.name} score ${value}`}
                className="shadow-none"
              >
                <Trash2 aria-hidden />
              </Button>
            }
            title={`Delete this ${score.name} score?`}
            description="The score is removed from the trace. This cannot be undone."
            confirmLabel="Delete score"
            onConfirm={() =>
              remove.mutateAsync(score.id).catch((error: unknown) => {
                toast.error(scoreErrorMessage(error, "delete"));
                throw error;
              })
            }
          />
        ) : null}
      </div>
      {score.comment ? (
        <p className="text-sm leading-5 [overflow-wrap:anywhere] whitespace-pre-wrap text-muted-foreground">
          {score.comment}
        </p>
      ) : null}
    </li>
  );
}
