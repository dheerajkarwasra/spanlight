import { Check, ThumbsDown, ThumbsUp } from "lucide-react";
import type { Ref } from "react";

import type { FeedbackVerdict } from "@/lib/api";
import { cn } from "@/lib/utils";

const LABELS: Record<FeedbackVerdict, string> = { right: "Right", wrong: "Wrong" };

interface VoteButtonProps {
  verdict: FeedbackVerdict;
  pressed: boolean;
  /** Set for a viewer: the button stays focusable and says why through `describedBy`. */
  describedBy: string | undefined;
  onClick: () => void;
  ref: Ref<HTMLButtonElement>;
}

/**
 * Figma "Vote · Right/Wrong": a 44 px pill. The pressed verdict gets a violet border, a tint and
 * a check, so it is never shown by colour alone. A viewer's buttons are `aria-disabled` rather
 * than `disabled`, so keyboard users still reach them and hear the reason.
 */
export function VoteButton({ verdict, pressed, describedBy, onClick, ref }: VoteButtonProps) {
  const Icon = verdict === "right" ? ThumbsUp : ThumbsDown;
  const disabled = describedBy !== undefined;
  return (
    <button
      ref={ref}
      type="button"
      aria-pressed={pressed}
      aria-disabled={disabled || undefined}
      aria-describedby={describedBy}
      onClick={disabled ? undefined : onClick}
      className={cn(
        "inline-flex h-11 shrink-0 items-center gap-2 rounded-full px-[18px] text-sm font-semibold",
        "transition-[background-color,color,border-color] duration-200 ease-out-quart",
        "focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring",
        "[&_svg]:pointer-events-none [&_svg]:shrink-0",
        pressed
          ? "border-2 border-accent bg-accent-subtle px-[17px] text-accent"
          : "border border-border-strong bg-surface text-foreground",
        disabled
          ? "cursor-not-allowed text-subtle-foreground"
          : !pressed && "hover:bg-surface-muted",
      )}
    >
      <Icon aria-hidden className="size-[18px]" />
      {LABELS[verdict]}
      {pressed ? <Check aria-hidden className="size-4" /> : null}
    </button>
  );
}
