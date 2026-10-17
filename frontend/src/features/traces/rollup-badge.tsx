import { Badge } from "@/components/ui/badge";

/**
 * Figma "Span usage card / rollup": the span repeats the usage of its child spans, so its tokens
 * and cost are shown as sent but left out of trace totals. Static content, not a live region.
 */
export function RollupBadge() {
  return <Badge variant="neutral">Counted on child spans</Badge>;
}
