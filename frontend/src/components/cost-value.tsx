import { CircleDashed } from "lucide-react";

import { UnknownValue, type UnknownTone } from "@/components/unknown-value";
import { Tooltip } from "@/components/ui/tooltip";
import { formatCost } from "@/lib/format";
import { cn } from "@/lib/utils";

export const NO_PRICE_REASON = "No price for this model. Set prices in Cost › Pricing.";
/** A total some of whose spans have no cost: no price for their model, or usage never reported. */
export const LOWER_BOUND_REASON =
  "Some spans have no known cost (no price, or usage not reported); total is a lower bound. Set prices in Cost › Pricing.";
/** A total none of whose spans has a known cost. */
export const UNKNOWN_TOTAL_REASON = "No span has a known cost (no price, or usage not reported)";
/** A cost of provider calls that is truly unknown: no price for the model, or no usage. */
export const UNPRICED_OR_NO_USAGE_REASON =
  "Unknown: this model has no price or the provider returned no usage. Set prices in Cost › Pricing.";

interface CostValueProps {
  cost: string | number | null;
  /** Some contributing spans had no price, so a known total is only a lower bound. */
  hasUnpriced?: boolean;
  /** Tooltip shown when the whole cost is unknown. */
  unknownReason?: string;
  /** Placeholder colours for an unknown cost; `on-ink` on the dark header bands. */
  unknownTone?: UnknownTone;
  className?: string;
}

export function CostValue({
  cost,
  hasUnpriced = false,
  unknownReason = NO_PRICE_REASON,
  unknownTone,
  className,
}: CostValueProps) {
  const formatted = formatCost(cost);
  if (formatted === null) {
    return <UnknownValue reason={unknownReason} tone={unknownTone} className={className} />;
  }
  return (
    <span className={cn("inline-flex items-center gap-1 tabular", className)}>
      {formatted}
      {hasUnpriced ? (
        <Tooltip content={LOWER_BOUND_REASON}>
          <span
            role="img"
            tabIndex={0}
            aria-label={LOWER_BOUND_REASON}
            className="inline-flex cursor-help rounded-sm text-warning"
          >
            <CircleDashed aria-hidden className="size-3" />
          </span>
        </Tooltip>
      ) : null}
    </span>
  );
}
