import type { InsightStatus } from "@/lib/api";

/** The reason a viewer sees under the disabled "Was this right?" buttons (`insights:feedback`). */
export const FEEDBACK_READ_ONLY_REASON = "Only members, admins and owners can give feedback";

/** Which actions apply to each status (acknowledge: open; resolve: not resolved; unmute: muted). */
export function availableActions(status: InsightStatus) {
  return {
    acknowledge: status === "open",
    resolve: status !== "resolved",
    mute: true,
    unmute: status === "muted",
  };
}
