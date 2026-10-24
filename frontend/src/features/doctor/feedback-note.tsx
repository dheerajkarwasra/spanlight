import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";

import { muteReasonFromNote, NOTE_MAX_LENGTH, noteCounter } from "./feedback-state";
import { MuteDialog } from "./mute-dialog";

interface FeedbackNoteProps {
  /** The note already stored with the vote, if any. */
  initial: string;
  saving: boolean;
  onSave: (note: string) => void;
  onSkip: () => void;
  /** Set after a Wrong vote for `insights:manage`: offers to mute the finding with this note. */
  muteInsightId?: string | undefined;
}

/**
 * Figma "Note field": an optional note (≤ 500 characters) under a saved vote. It takes focus
 * when it opens; Skip closes it and the vote stays as saved. After a Wrong vote an admin can
 * also mute the finding, the reason prefilled from the note.
 */
export function FeedbackNote({
  initial,
  saving,
  onSave,
  onSkip,
  muteInsightId,
}: FeedbackNoteProps) {
  const fieldId = useId();
  const metaId = useId();
  const [note, setNote] = useState(initial);
  const [muting, setMuting] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    field.current?.focus();
  }, []);

  return (
    <div className="flex w-full flex-col gap-1.5">
      <label htmlFor={fieldId} className="text-sm font-semibold text-foreground">
        Add a note (optional)
      </label>
      <Textarea
        ref={field}
        id={fieldId}
        value={note}
        maxLength={NOTE_MAX_LENGTH}
        rows={3}
        aria-describedby={metaId}
        onChange={(event) => {
          setNote(event.target.value);
        }}
        className="min-h-22"
      />
      <div id={metaId} className="flex items-start gap-2 text-xs font-medium text-muted-foreground">
        <p className="min-w-0 flex-1">
          Shown as plain text to project members. {NOTE_MAX_LENGTH} characters at most.
        </p>
        <p className="shrink-0 tabular">{noteCounter(note)}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <Button
          variant="primary"
          className="max-md:h-11"
          loading={saving}
          onClick={() => {
            onSave(note);
          }}
        >
          Save note
        </Button>
        <Button variant="ghost" className="max-md:h-11" disabled={saving} onClick={onSkip}>
          Skip
        </Button>
        {muteInsightId === undefined ? null : (
          <Button
            className="max-md:h-11"
            onClick={() => {
              setMuting(true);
            }}
          >
            Mute this finding
          </Button>
        )}
      </div>
      {muteInsightId === undefined ? null : (
        <MuteDialog
          insightId={muteInsightId}
          open={muting}
          onOpenChange={setMuting}
          defaultReason={muteReasonFromNote(note)}
        />
      )}
    </div>
  );
}
