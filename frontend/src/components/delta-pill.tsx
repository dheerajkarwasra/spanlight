import { ArrowDown, ArrowUp, Minus } from "lucide-react";

import { deltaTone, signedDelta, type DeltaTone } from "@/lib/delta";
import { cn } from "@/lib/utils";

type DeltaPillSurface = "default" | "ink" | "accent";

export interface DeltaPillProps {
  /** Signed change versus the previous period. `null` renders nothing (no comparison). */
  delta: number | null;
  /**
   * True when more is better; false for errors, latency and cost. `null` for volume metrics
   * (calls, tokens): a change is neither good nor bad, so the pill stays neutral and only the
   * comparison is announced.
   */
  increaseIsGood: boolean | null;
  /** Formats the absolute change (default: one-decimal percent). The sign is added for you. */
  format?: (absolute: number) => string;
  /**
   * `default` for white cards. `ink` for hero cards: good changes turn lime, bad ones rose
   * on a translucent tile. `accent` for the violet gradient card: always an ink pill (that card
   * only carries volume metrics, which have no good/bad tone).
   */
  surface?: DeltaPillSurface;
  /** Puts an arrow (up, down or flat) before the text, so direction never rides on colour alone. */
  showArrow?: boolean;
  /** Appended for screen readers, e.g. "vs previous 24 hours". */
  comparisonLabel?: string;
  className?: string;
}

const ACCENT_PILL = "bg-hero-card text-hero-card-foreground";

const TONE_CLASSES: Record<DeltaPillSurface, Record<DeltaTone, string>> = {
  default: {
    good: "bg-success-subtle text-success",
    bad: "bg-danger-subtle text-danger-text",
    neutral: "bg-surface-muted text-muted-foreground",
  },
  ink: {
    good: "bg-lime text-lime-foreground",
    bad: "bg-rail-tile text-rail-danger",
    neutral: "bg-rail-tile text-rail-muted-foreground",
  },
  accent: { good: ACCENT_PILL, bad: ACCENT_PILL, neutral: ACCENT_PILL },
};

const TONE_WORDS: Record<DeltaTone, string> = {
  good: "improvement",
  bad: "regression",
  neutral: "no change",
};

function defaultFormat(absolute: number): string {
  return `${absolute.toFixed(1)}%`;
}

/**
 * A change-vs-previous-period pill (e.g. "−12.4%"). Colour follows whether the change is good
 * news, not its sign, and the tone is also spelled out for screen readers.
 */
export function DeltaPill({
  delta,
  increaseIsGood,
  format = defaultFormat,
  surface = "default",
  showArrow = false,
  comparisonLabel,
  className,
}: DeltaPillProps) {
  if (delta === null || !Number.isFinite(delta)) {
    return null;
  }
  const tone = deltaTone(delta, increaseIsGood);
  // Without a direction "neutral" doesn't mean "no change", so there is no tone to announce.
  const toneWord = increaseIsGood === null ? null : TONE_WORDS[tone];
  const Arrow = delta > 0 ? ArrowUp : delta < 0 ? ArrowDown : Minus;
  const srSuffix = [toneWord, comparisonLabel].filter(Boolean).join(", ");

  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap tabular",
        TONE_CLASSES[surface][tone],
        className,
      )}
    >
      {showArrow ? <Arrow aria-hidden className="size-3" strokeWidth={2.5} /> : null}
      {signedDelta(delta, format)}
      {srSuffix ? <span className="sr-only"> ({srSuffix})</span> : null}
    </span>
  );
}
