import { NO_PRICE_REASON } from "@/components/cost-value";
import type { UsageState } from "@/lib/api";

/** A usage cell of the span usage card whose "—" may need a reason. */
export type UsageField = "input" | "output" | "cost";

const NO_TOKEN_COUNTS = "The exporter sent no token counts";

/**
 * The tooltip copy of an unknown usage cell, by usage state (spec §6). Null when the state does
 * not explain that cell: the value is shown, or a rollup, whose badge carries the copy.
 */
export function usageReason(state: UsageState, field: UsageField): string | null {
  switch (state) {
    case "usage_not_reported":
      return NO_TOKEN_COUNTS;
    case "partial_usage":
      if (field === "cost") {
        return "Usage is incomplete, so no cost was computed";
      }
      return field === "input"
        ? "The exporter sent only output tokens"
        : "The exporter sent only input tokens";
    case "model_unknown":
      return field === "cost" ? "No model name on this span" : null;
    case "no_price":
      return field === "cost" ? NO_PRICE_REASON : null;
    case "priced":
    case "rollup":
    case "not_applicable":
      return null;
  }
}
