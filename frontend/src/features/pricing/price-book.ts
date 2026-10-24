import type { Price } from "@/lib/api";

/*
 * The price book on the Pricing page (spec ruling 8): the newest version of each provider and
 * pattern, searched on the client by provider or pattern, rates per 1M tokens.
 */

/**
 * The rows of each `(provider, model_pattern)`'s newest `effective_from`, every prompt tier of it
 * kept, ordered by provider, pattern, then tier.
 */
export function latestByPattern(rows: readonly Price[]): Price[] {
  const newest = new Map<string, number>();
  const keyOf = (row: Price) => `${row.provider}\u0000${row.model_pattern}`;
  for (const row of rows) {
    const at = Date.parse(row.effective_from);
    newest.set(keyOf(row), Math.max(newest.get(keyOf(row)) ?? -Infinity, at));
  }
  return rows
    .filter((row) => Date.parse(row.effective_from) === newest.get(keyOf(row)))
    .sort(
      (a, b) =>
        a.provider.localeCompare(b.provider) ||
        a.model_pattern.localeCompare(b.model_pattern) ||
        a.min_prompt_tokens - b.min_prompt_tokens,
    );
}

/** Rows whose provider or pattern contains the search, ignoring case; all rows for a blank one. */
export function filterBook<Row extends Pick<Price, "provider" | "model_pattern">>(
  rows: readonly Row[],
  search: string,
): Row[] {
  const needle = search.trim().toLowerCase();
  return rows.filter(
    (row) =>
      row.provider.toLowerCase().includes(needle) ||
      row.model_pattern.toLowerCase().includes(needle),
  );
}

/**
 * A rate per 1M tokens as the book states it, never rounded: "$3.00", "$0.025", "$1,234.50".
 * Null stays null (no separate rate).
 */
export function formatRate(value: string | null): string | null {
  if (value === null) {
    return null;
  }
  const [whole = "0", fraction = ""] = value.split(".");
  const decimals = fraction.replace(/0+$/, "").padEnd(2, "0");
  const grouped = (whole.replace(/^0+(?=\d)/, "") || "0").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `$${grouped}.${decimals}`;
}
