import { FreshnessChip, type FreshnessChipProps } from "@/components/freshness-chip";

import { COMPUTED_AT_EXPLANATION, NOT_COMPUTED_EXPLANATION } from "./computed-at";

const EXPLANATION = { computed: COMPUTED_AT_EXPLANATION, notComputed: NOT_COMPUTED_EXPLANATION };

/** The freshness chip of the Users pages, explaining the per-user refresh schedule. */
export function ComputedAtChip(props: Omit<FreshnessChipProps, "explanation">) {
  return <FreshnessChip {...props} explanation={EXPLANATION} />;
}
