import { isApiError, NetworkError, type FeedbackVerdict } from "@/lib/api";
import { formatInteger } from "@/lib/format";

/*
 * The "Was this right?" row's wording and transitions. A member's vote is saved the moment a
 * verdict is clicked; clicking the pressed verdict again takes the vote back.
 */

/** The server's limit on a vote's note. */
export const NOTE_MAX_LENGTH = 500;

/** The vote after a click: the clicked verdict, or none when it was already the current one. */
export function nextVerdict(
  current: FeedbackVerdict | null,
  clicked: FeedbackVerdict,
): FeedbackVerdict | null {
  return current === clicked ? null : clicked;
}

const sameYearDay = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const otherYearDay = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
});

/** "You said this was right on 14 Oct"; without a readable time the sentence ends at the verdict. */
export function feedbackLabel(
  mine: { verdict: FeedbackVerdict; updatedAt: string | null },
  now: Date,
): string {
  const sentence = `You said this was ${mine.verdict}`;
  const at = mine.updatedAt === null ? Number.NaN : Date.parse(mine.updatedAt);
  if (Number.isNaN(at)) {
    return sentence;
  }
  const date = new Date(at);
  const format = date.getFullYear() === now.getFullYear() ? sameYearDay : otherYearDay;
  return `${sentence} on ${format.format(date)}`;
}

/** Every member's votes: "2 said right · 0 said wrong". */
export function countsLine(right: number, wrong: number): string {
  const said = (count: number) => formatInteger(count) ?? String(count);
  return `${said(right)} said right · ${said(wrong)} said wrong`;
}

export interface FeedbackStatusInput {
  mine: FeedbackVerdict | null;
  /** When the vote was saved on this page; null when it is not known. */
  savedAt: string | null;
  noteOpen: boolean;
  canVote: boolean;
  right: number;
  wrong: number;
}

/**
 * The line under the question, for each state of the Figma "Feedback row", in two parts:
 * `own` is about the caller's vote and is announced when it changes; `counts` are everyone's
 * votes, which the minute poll refreshes, so they are never announced.
 */
export function feedbackStatusParts(
  input: FeedbackStatusInput,
  now: Date,
): { own: string | null; counts: string | null } {
  const counts = `${countsLine(input.right, input.wrong)}.`;
  if (input.mine === null) {
    return {
      own: input.canVote ? "Your answer measures how often this check is right." : null,
      counts,
    };
  }
  if (input.noteOpen) {
    return { own: `Saved: you said this was ${input.mine}.`, counts };
  }
  return {
    own: feedbackLabel({ verdict: input.mine, updatedAt: input.savedAt }, now),
    counts: null,
  };
}

/** "53 / 500": the note's length against the limit. */
export function noteCounter(note: string): string {
  return `${String(note.length)} / ${String(NOTE_MAX_LENGTH)}`;
}

/** The inline error when a vote or its removal failed. */
export function feedbackErrorMessage(action: "save" | "remove", error: unknown): string {
  const lead = action === "save" ? "Your answer wasn’t saved" : "Your answer wasn’t removed";
  if (isApiError(error)) {
    return `${lead}: the server answered ${String(error.status)}. Try again.`;
  }
  if (error instanceof NetworkError) {
    return `${lead}: the server could not be reached. Try again.`;
  }
  return `${lead}. Try again.`;
}
