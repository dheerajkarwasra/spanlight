import type { CompareStep } from "@/lib/api";

/** How many steps only one trace has: the compare page's "Steps only in A / B" tile. */
export function onlyInCounts(steps: readonly CompareStep[]): { a: number; b: number } {
  return {
    a: steps.filter((step) => step.b === null).length,
    b: steps.filter((step) => step.a === null).length,
  };
}
