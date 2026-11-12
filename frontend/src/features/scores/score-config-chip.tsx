import type { ScoreConfig } from "@/lib/api";
import { configChipSummary, configChipText } from "@/lib/score-format";

/**
 * The pill beside a score name: its type and allowed values on one line ("numeric · 0 to 1"). A
 * long category list is counted ("categorical · 11 values") so the pill never wraps; the full
 * list is the pill's tooltip.
 */
export function ScoreConfigChip({ config }: { config: ScoreConfig }) {
  const summary = configChipSummary(config);
  const full = configChipText(config);
  return (
    <span
      title={full === summary ? undefined : full}
      className="max-w-full truncate rounded-full border border-border-strong bg-surface px-[9px] py-[3px] text-2xs font-medium text-muted-foreground"
    >
      {summary}
    </span>
  );
}
