import { UNKNOWN_DASH_CLASS, UnknownValue } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import type { VarianceTone } from "@/features/traces";
import { cn } from "@/lib/utils";

import { REASONS, type Figure } from "./reconciliation-figures";

interface FigureTextProps {
  figure: Figure;
  className?: string;
  /**
   * Inside a button (the mobile day tile) the dash can't be its own focusable tooltip trigger:
   * it is drawn plain and its reason is read with the button's name.
   */
  plain?: boolean;
}

/** A money or token figure, or "—" with the reason it is unknown (Figma: text/subtle). */
export function FigureText({ figure, className, plain = false }: FigureTextProps) {
  if (figure.text !== null) {
    return <span className={className}>{figure.text}</span>;
  }
  const reason = figure.reason ?? REASONS.notFetched;
  if (plain) {
    return (
      <span className={className}>
        <span aria-hidden className={UNKNOWN_DASH_CLASS}>
          —
        </span>
        <span className="sr-only">{reason}</span>
      </span>
    );
  }
  return <UnknownValue reason={reason} className={className} />;
}

const TONE_VARIANTS = {
  within2: "success",
  within10: "warning",
  over: "danger",
  unknown: "neutral",
} as const satisfies Record<VarianceTone, string>;

interface VarianceBadgeProps {
  text: string;
  tone: VarianceTone;
  /** "Variance +3.1 percent, 2 to 10 percent band": the band in words, not in colour alone. */
  accessibleName: string;
  className?: string;
}

/** Figma "Badge" in the variance tones: ≤ 2 % success, ≤ 10 % warning, above danger. */
export function VarianceBadge({ text, tone, accessibleName, className }: VarianceBadgeProps) {
  return (
    <Badge variant={TONE_VARIANTS[tone]} className={className}>
      <span aria-hidden>{text}</span>
      <span className="sr-only">{accessibleName}</span>
    </Badge>
  );
}

/** The variance of a row: its badge, or "—" with the reason. */
export function VarianceCell({
  figure,
  plain = false,
}: {
  figure: Figure & { tone: VarianceTone; accessibleName: string | null };
  plain?: boolean;
}) {
  if (figure.text === null || figure.accessibleName === null) {
    return <FigureText figure={figure} plain={plain} className="font-mono text-label" />;
  }
  return (
    <VarianceBadge text={figure.text} tone={figure.tone} accessibleName={figure.accessibleName} />
  );
}

/** The legend above the table (Figma "Variance legend"): the three bands named in words. */
export function VarianceLegend({ className }: { className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      <span className="text-overline text-subtle-foreground uppercase">Variance</span>
      <Badge variant="success">≤ 2 %</Badge>
      <Badge variant="warning">2–10 %</Badge>
      <Badge variant="danger">&gt; 10 %</Badge>
    </div>
  );
}
