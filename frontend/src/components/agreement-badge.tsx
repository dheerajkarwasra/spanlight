import { TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import type { AgreementSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

import { badgeLabel, badgeText } from "./agreement-format";

const KAPPA_TOOLTIP = "Kappa: 1 is perfect agreement with reviewers, 0 is no better than chance.";

interface AgreementBadgeProps {
  summary: AgreementSummary | undefined;
  /**
   * Inside a link (a run row): no focus stop or tooltip, since nested interactive content is
   * invalid; the sentence is kept for screen readers.
   */
  inLink?: boolean;
}

/**
 * Figma "AgreementBadge": how far a judge agrees with reviewers, beside its mean. Neutral, weak
 * (TriangleAlert and the word) or not calibrated (muted). Nothing without a summary.
 */
export function AgreementBadge({ summary, inLink = false }: AgreementBadgeProps) {
  const text = summary ? badgeText(summary) : null;
  if (summary === undefined || text === null) {
    return null;
  }
  const weak = summary.status === "ok" && summary.flagged;
  const label = badgeLabel(summary);
  const classes = cn(summary.status === "not_enough_pairs" && "text-muted-foreground");
  const icon = weak ? <TriangleAlert aria-hidden /> : null;
  if (inLink) {
    return (
      <Badge variant={weak ? "warning" : "outline"} size="sm" className={classes}>
        {icon}
        <span aria-hidden>{text}</span>
        <span className="sr-only">{label}</span>
      </Badge>
    );
  }
  return (
    <Tooltip content={KAPPA_TOOLTIP}>
      <Badge
        variant={weak ? "warning" : "outline"}
        tabIndex={0}
        aria-label={label}
        className={cn("cursor-help", classes)}
      >
        {icon}
        {text}
      </Badge>
    </Tooltip>
  );
}

/** The summary request failed, beside a mean known to be a judge's: say so rather than nothing. */
export function AgreementUnavailableBadge({ inLink = false }: { inLink?: boolean }) {
  return (
    <Badge variant="outline" size={inLink ? "sm" : "md"} className="text-muted-foreground">
      <span aria-hidden>Agreement unavailable</span>
      <span className="sr-only">Judge agreement with reviewers couldn&apos;t load</span>
    </Badge>
  );
}
