import { formatUsd } from "./budget-period";

/** "$182", "$182.40": whole dollars drop the cents, so chart labels stay short. */
export function shortUsd(value: number): string {
  return (formatUsd(String(value)) ?? "").replace(/\.00$/, "");
}

/** Spec §3.6: the progress bar's tick marks at 50, 80 and 100 % of the amount. */
export function tickPositions(amount: number): { pct: 50 | 80 | 100; usd: string }[] {
  return ([50, 80, 100] as const).map((pct) => ({ pct, usd: shortUsd((amount * pct) / 100) }));
}
