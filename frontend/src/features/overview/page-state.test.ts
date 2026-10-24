import { describe, expect, it } from "vitest";

import { statusFor } from "./page-state";
import type { OverviewQueries } from "./use-overview-queries";

/** Just the parts `statusFor` reads: 100 traces, `errors` of 100 calls failed, and the health query. */
function queries(errors: number, health: Partial<OverviewQueries["health"]>): OverviewQueries {
  return {
    overview: { data: { current: { traces: 100, llm_calls: 100, error_rate: errors / 100 } } },
    health,
  } as unknown as OverviewQueries;
}

describe("statusFor", () => {
  it("keeps the status line loading until the open findings are known", () => {
    expect(statusFor("ready", queries(0, { isPending: true, data: undefined }))).toBeNull();
  });

  it("falls back to the error-only sentence when the health check failed", () => {
    expect(
      statusFor("ready", queries(15, { isPending: false, isError: true, data: undefined })),
    ).toMatchObject({ lead: "15 errors", accent: "need a look." });
    expect(
      statusFor("ready", queries(0, { isPending: false, isError: true, data: undefined }))?.kind,
    ).toBe("healthy");
  });

  it("adds the open findings once health has loaded", () => {
    const health = { isPending: false, data: { open_critical: 1, open_warning: 0 } };
    expect(
      statusFor("ready", queries(15, health as Partial<OverviewQueries["health"]>)),
    ).toMatchObject({ lead: "15 errors and 1 finding", accent: "need a look." });
  });
});
