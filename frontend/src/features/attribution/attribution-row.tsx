import { Link } from "@tanstack/react-router";
import { CircleDashed } from "lucide-react";
import type { ReactNode } from "react";

import { CostValue, LOWER_BOUND_REASON } from "@/components/cost-value";
import { UNKNOWN_DASH_CLASS, UnknownValue } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import { useProjectParams } from "@/features/shell/project-context";
import type { AttributionTotals } from "@/lib/api";
import { formatCost, formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  NO_GROUP_PRICE,
  NO_RETRIES,
  RETRIES_NOT_COMPUTED,
  REDACTED,
  RETRY_UNPRICED,
} from "./attribution-grid";
import type { TraceDrillSearch } from "./drill-down";
import { formatShare, shareBarWidth } from "./share-format";

/** The violet mono link of a value or "none" row, with a 24 px hit box inside the row. */
export const VALUE_LINK_CLASS =
  "inline-flex min-h-6 min-w-0 items-center rounded-sm font-mono text-code text-accent hover:underline";

interface DrillLinkProps {
  search: TraceDrillSearch;
  className: string;
  title?: string;
  children: ReactNode;
}

/** A link to the trace list filtered to one group, over the same window and environment. */
export function DrillLink({ search, className, title, children }: DrillLinkProps) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/traces"
      params={{ orgId, projectId }}
      search={search}
      title={title}
      className={className}
    >
      {children}
    </Link>
  );
}

/** A value as text: monospace and truncated, or the "redacted" badge for `[REDACTED]`. */
export function ValueLabel({ value }: { value: string }) {
  if (value === REDACTED) {
    return (
      <Badge variant="warning" size="sm" title={REDACTED}>
        redacted
      </Badge>
    );
  }
  return <span className="truncate">{value}</span>;
}

interface PlainProps {
  /**
   * Inside a whole-tile link: no focusable tooltip triggers (nested interactive content), the
   * reason is read out instead.
   */
  plain?: boolean;
}

/** The unknown dash without its own tab stop, for content inside a link. */
function PlainUnknown({ reason }: { reason: string }) {
  return (
    <span title={reason} className={UNKNOWN_DASH_CLASS}>
      <span aria-hidden>—</span>
      <span className="sr-only">{`Unknown: ${reason}`}</span>
    </span>
  );
}

/** The percentage and its 4 px bar; the bar repeats the number, so it is hidden from readers. */
export function ShareCell({
  share,
  track = "w-14",
  plain = false,
}: PlainProps & { share: number | null; track?: string }) {
  const width = shareBarWidth(share);
  if (width === null) {
    return plain ? (
      <PlainUnknown reason={NO_GROUP_PRICE} />
    ) : (
      <UnknownValue reason={NO_GROUP_PRICE} />
    );
  }
  return (
    <span className="inline-flex items-center gap-2 tabular">
      <span className="w-11 text-right">{formatShare(share)}</span>
      <span aria-hidden className={cn("h-1 overflow-hidden rounded-xs bg-accent-subtle", track)}>
        <span className="block h-full rounded-xs bg-accent" style={{ width }} />
      </span>
    </span>
  );
}

export function GroupCost({ figures, plain = false }: PlainProps & { figures: AttributionTotals }) {
  if (plain) {
    const cost = formatCost(figures.cost_usd);
    if (cost === null) {
      return <PlainUnknown reason={NO_GROUP_PRICE} />;
    }
    return (
      <span className="inline-flex items-center gap-1 tabular">
        {cost}
        {figures.unpriced_calls > 0 ? (
          <CircleDashed
            role="img"
            aria-label={LOWER_BOUND_REASON}
            className="size-3 text-warning"
          />
        ) : null}
      </span>
    );
  }
  return (
    <CostValue
      cost={figures.cost_usd}
      hasUnpriced={figures.unpriced_calls > 0}
      unknownReason={NO_GROUP_PRICE}
    />
  );
}

export function ErrorCount({ count }: { count: number }) {
  return (
    <span className={cn("tabular", count > 0 && "text-danger-text")}>{formatInteger(count)}</span>
  );
}

export function RetryCost({ figures }: { figures: AttributionTotals }) {
  const cost = formatCost(figures.retry_cost_usd);
  if (cost === null) {
    let reason = NO_RETRIES;
    if (figures.retries === null) {
      reason = RETRIES_NOT_COMPUTED;
    } else if (figures.retries > 0) {
      reason = RETRY_UNPRICED;
    }
    return <UnknownValue reason={reason} />;
  }
  return <span className="tabular">{cost}</span>;
}
