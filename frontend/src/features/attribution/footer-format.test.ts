import { describe, expect, it } from "vitest";

import type { AttributionBucket } from "@/lib/api";

import type { AttributionView } from "./attribution-grid";
import { shareOf } from "./footer-format";
import { formatShare } from "./share-format";

function view(total: string | null): AttributionView {
  return { page: { totals: { cost_usd: total } } } as unknown as AttributionView;
}

function bucket(cost: string | null): AttributionBucket {
  return { cost_usd: cost } as unknown as AttributionBucket;
}

describe("shareOf", () => {
  it("keeps a tiny bucket above zero so it never reads as free", () => {
    const share = shareOf(bucket("0.0004"), view("10"));
    expect(share).toBeGreaterThan(0);
    expect(formatShare(share ?? 0)).toBe("<0.1%");
  });

  it("rounds an ordinary share like the API and keeps unknown as null", () => {
    expect(shareOf(bucket("1"), view("3"))).toBe(0.3333);
    expect(shareOf(bucket(null), view("3"))).toBeNull();
    expect(shareOf(bucket("1"), view("0"))).toBeNull();
  });
});
