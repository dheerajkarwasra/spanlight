import { CircleAlert } from "lucide-react";
import { useId, useRef, useState } from "react";

import { ReadOnlyLine } from "@/components/read-only-line";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { FeedbackVerdict, InsightFeedback } from "@/lib/api";
import { useNow } from "@/lib/use-now";

import { FEEDBACK_READ_ONLY_REASON } from "./action-rules";
import { useDeleteFeedback, usePutFeedback } from "./doctor-queries";
import { FeedbackNote } from "./feedback-note";
import { feedbackErrorMessage, feedbackStatusParts, nextVerdict } from "./feedback-state";
import { VoteButton } from "./feedback-vote-button";

interface FeedbackRowProps {
  insightId: string;
  feedback: InsightFeedback;
  /** `insights:feedback`: members and above. */
  canVote: boolean;
  /** `insights:manage`: a Wrong vote's note then offers to mute the finding. */
  canManage: boolean;
}

/**
 * The vote this row last saved, and the served vote it replaced. It shows until the served vote
 * moves off that basis (the refetch after the save), so the row answers at once.
 */
interface LocalVote {
  basis: FeedbackVerdict | null;
  verdict: FeedbackVerdict | null;
}

/**
 * Figma "Doctor/Insight detail/Feedback": "Was this right?" with Right and Wrong. A click saves
 * the vote at once and opens an optional note; clicking the pressed verdict, or "Remove my
 * answer", takes it back. Viewers see the row disabled with the reason as visible text.
 */
export function FeedbackRow({ insightId, feedback, canVote, canManage }: FeedbackRowProps) {
  const titleId = useId();
  const reasonId = useId();
  const now = useNow();
  const put = usePutFeedback(insightId);
  const remove = useDeleteFeedback(insightId);
  const [local, setLocal] = useState<LocalVote | null>(null);
  const [noteOpen, setNoteOpen] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const rightButton = useRef<HTMLButtonElement>(null);
  const wrongButton = useRef<HTMLButtonElement>(null);

  const served = feedback.mine?.verdict ?? null;
  const mine = local !== null && local.basis === served ? local.verdict : served;
  // The served vote's time, or this page's last save while the refetch is on its way.
  const savedAt =
    put.data?.verdict === mine
      ? put.data.updated_at
      : mine === served
        ? (feedback.mine?.updated_at ?? null)
        : null;
  const busy = put.isPending || remove.isPending;
  const showNote = noteOpen && mine !== null;

  function focusVote(verdict: FeedbackVerdict | null) {
    (verdict === "wrong" ? wrongButton : rightButton).current?.focus();
  }

  function save(verdict: FeedbackVerdict, note: string | undefined) {
    setFailure(null);
    const input = note === undefined ? { verdict } : { verdict, note };
    put.mutate(input, {
      onSuccess: () => {
        setLocal({ basis: served, verdict });
        // The first save opens the note; saving the note closes it.
        setNoteOpen(note === undefined);
        if (note !== undefined) {
          focusVote(verdict);
        }
      },
      onError: (error) => {
        setFailure(feedbackErrorMessage("save", error));
      },
    });
  }

  function takeBack() {
    setFailure(null);
    remove.mutate(undefined, {
      onSuccess: () => {
        setLocal({ basis: served, verdict: null });
        setNoteOpen(false);
        put.reset();
        focusVote("right");
      },
      onError: (error) => {
        setFailure(feedbackErrorMessage("remove", error));
      },
    });
  }

  function vote(clicked: FeedbackVerdict) {
    if (busy) {
      return;
    }
    const next = nextVerdict(mine, clicked);
    if (next === null) {
      takeBack();
    } else {
      save(next, undefined);
    }
  }

  const describedBy = canVote ? undefined : reasonId;
  const status = feedbackStatusParts(
    { mine, savedAt, noteOpen: showNote, canVote, right: feedback.right, wrong: feedback.wrong },
    now,
  );

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-3 p-5 sm:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-60 flex-1 flex-col gap-0.5">
          <h2
            id={titleId}
            className={canVote ? "text-card text-foreground" : "text-card text-muted-foreground"}
          >
            Was this right?
          </h2>
          <p className="text-xs font-medium text-muted-foreground">
            {/* Only the caller's own vote is announced; the polled counts would chatter. */}
            <span aria-live="polite">{status.own}</span>
            {status.own !== null && status.counts !== null ? " " : null}
            {status.counts}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <VoteButton
            ref={rightButton}
            verdict="right"
            pressed={mine === "right"}
            describedBy={describedBy}
            onClick={() => {
              vote("right");
            }}
          />
          <VoteButton
            ref={wrongButton}
            verdict="wrong"
            pressed={mine === "wrong"}
            describedBy={describedBy}
            onClick={() => {
              vote("wrong");
            }}
          />
          {canVote && mine !== null && !showNote ? (
            <Button
              variant="ghost"
              className="max-md:h-11"
              loading={remove.isPending}
              onClick={takeBack}
            >
              Remove my answer
            </Button>
          ) : null}
        </div>
      </div>
      {showNote ? (
        <FeedbackNote
          initial={put.data?.note ?? ""}
          saving={put.isPending}
          muteInsightId={canManage && mine === "wrong" ? insightId : undefined}
          onSave={(note) => {
            save(mine, note);
          }}
          onSkip={() => {
            setNoteOpen(false);
            focusVote(mine);
          }}
        />
      ) : null}
      {canVote ? null : <ReadOnlyLine id={reasonId}>{FEEDBACK_READ_ONLY_REASON}</ReadOnlyLine>}
      {failure === null ? null : (
        <p
          role="alert"
          className="flex items-center gap-2 rounded-tile bg-danger-subtle p-3 text-sm font-medium text-danger-text"
        >
          <CircleAlert aria-hidden className="size-4 shrink-0" />
          {failure}
        </p>
      )}
    </Card>
  );
}
