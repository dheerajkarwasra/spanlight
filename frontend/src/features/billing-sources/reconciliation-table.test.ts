import { describe, expect, it } from "vitest";

import type {
  ReconciliationDay,
  ReconciliationGroup,
  ReconciliationReport,
  ReconciliationTokens,
} from "@/lib/api";

import { flattenDays, pendingDayRanges, renderRules, summaryTiles } from "./reconciliation-table";

const UNKNOWN_TOKENS: ReconciliationTokens = {
  uncached_input: null,
  cache_read: null,
  cache_write_5m: null,
  cache_write_1h: null,
  output: null,
};

/** The group of the spec §8.3 example. */
function group(overrides: Partial<ReconciliationGroup> = {}): ReconciliationGroup {
  return {
    group: "claude-sonnet-4-6",
    model_pattern: "claude-sonnet-4-6",
    provider_models: ["claude-sonnet-4-6"],
    spanlight_models: ["claude-sonnet-4-6-20260301"],
    provider_usd: "12.3456",
    spanlight_usd: "12.2000",
    explained_usd: "0.1000",
    residual_usd: "0.0456",
    variance_ratio: "0.0037",
    provider_tokens: {
      uncached_input: 1,
      cache_read: 2,
      cache_write_5m: 3,
      cache_write_1h: 0,
      output: 4,
    },
    spanlight_tokens: {
      uncached_input: 1,
      cache_read: 2,
      cache_write_5m: 1,
      cache_write_1h: 0,
      output: 5,
    },
    explained: [
      {
        kind: "server_tool_fees",
        usd: "0.1000",
        count: 10,
        tokens: null,
        note: "10 web searches on the provider side, 0 recorded",
      },
      {
        kind: "usage_not_reported",
        usd: null,
        count: 3,
        tokens: null,
        note: "3 calls whose output tokens were never reported (2 client disconnects)",
      },
    ],
    note: null,
    ...overrides,
  };
}

/** The day of the spec §8.3 example: settled, fetched, no credits or other charges. */
function day(overrides: Partial<ReconciliationDay> = {}): ReconciliationDay {
  return {
    day: "2026-10-01",
    provisional: false,
    fetched_at: "2026-10-04T03:30:00Z",
    incomplete: false,
    note: null,
    provider_usd: "12.3456",
    spanlight_usd: "12.2000",
    explained_usd: "0.1000",
    residual_usd: "0.0456",
    variance_ratio: "0.0037",
    credits_usd: "0",
    other_usd: "0.0000",
    unattributed_calls: 0,
    groups: [group()],
    credits: [],
    other: [],
    ...overrides,
  };
}

/** A provisional day with a credit, another charge and a priority commitment. */
const PROVISIONAL = day({
  day: "2026-10-02",
  provisional: true,
  fetched_at: "2026-10-03T03:30:00Z",
  provider_usd: "10.0000",
  spanlight_usd: "9.5000",
  explained_usd: "-0.2100",
  residual_usd: "0.7100",
  variance_ratio: "0.0710",
  credits_usd: "-5.0000",
  other_usd: "0.0200",
  groups: [
    group({
      explained: [
        {
          kind: "priority_commitment",
          usd: "-0.3100",
          count: 12,
          tokens: 184_200,
          note: "12 priority calls priced at the standard rate ($0.31); commitment-billed, not in the provider cost report (184,200 priority tokens)",
        },
        {
          kind: "unmodelled_modifiers",
          usd: null,
          count: 4,
          tokens: null,
          note: "4 calls priced at the standard rate with a modifier Spanlight does not price",
        },
      ],
    }),
  ],
  credits: [{ description: "Promotional credit", usd: "-5.0000" }],
  other: [{ description: "Code Execution Usage", usd: "0.0200" }],
});

/** No successful fetch covers this day yet: the provider side is unknown. */
const NOT_FETCHED = day({
  day: "2026-10-03",
  provisional: true,
  fetched_at: null,
  provider_usd: null,
  spanlight_usd: "2.3100",
  explained_usd: null,
  residual_usd: null,
  variance_ratio: null,
  credits_usd: null,
  other_usd: null,
  groups: [
    group({
      provider_models: [],
      provider_usd: null,
      explained_usd: null,
      residual_usd: null,
      variance_ratio: null,
      provider_tokens: UNKNOWN_TOKENS,
      explained: [],
    }),
  ],
});

/** The rollups of this day are still being built: the Spanlight side is unknown. */
const PENDING = day({
  day: "2026-10-04",
  provisional: true,
  incomplete: true,
  note: "rollup pending",
  spanlight_usd: null,
  explained_usd: null,
  residual_usd: null,
  variance_ratio: null,
  groups: [
    group({
      spanlight_models: [],
      spanlight_usd: null,
      explained_usd: null,
      residual_usd: null,
      variance_ratio: null,
      spanlight_tokens: UNKNOWN_TOKENS,
      explained: [],
    }),
  ],
});

/** The spans of this day are past retention, and none of the rest had a price. */
const RETENTION = day({
  day: "2026-09-20",
  incomplete: true,
  spanlight_usd: null,
  groups: [
    group({
      spanlight_usd: null,
      explained: [
        {
          kind: "beyond_retention",
          usd: null,
          count: null,
          tokens: null,
          note: "spans for this day are past the retention of Support Copilot",
        },
      ],
    }),
  ],
});

function report(days: ReconciliationDay[], summary?: ReconciliationReport["summary"]) {
  return {
    source: { id: "src_1", provider: "anthropic", kind: "anthropic_admin_api" },
    window: { start: "2026-10-01T00:00:00Z", end: "2026-10-04T00:00:00Z" },
    projects: [
      { id: "p1", name: "Support Copilot" },
      { id: "p2", name: "Eval harness" },
    ],
    days,
    summary: summary ?? {
      days: days.length,
      settled_days: days.filter((one) => !one.provisional).length,
      days_within_2pct: 1,
      max_abs_variance_ratio: "0.0037",
    },
  } satisfies ReconciliationReport;
}

describe("flattenDays", () => {
  const rows = flattenDays(report([day(), PROVISIONAL, NOT_FETCHED]), "p1");

  it("lists the newest day first, labelled without a year", () => {
    expect(rows.map((row) => row.label)).toEqual(["Oct 3", "Oct 2", "Oct 1"]);
  });

  it("formats the day's figures and its variance band", () => {
    const [, provisional, settled] = rows;
    expect(settled?.provider).toEqual({ text: "$12.35", reason: null });
    expect(settled?.spanlight).toEqual({ text: "$12.20", reason: null });
    expect(settled?.explained).toEqual({ text: "$0.10", reason: null });
    expect(settled?.residual).toEqual({ text: "+$0.05", reason: null });
    expect(settled?.variance).toMatchObject({
      text: "+0.4 %",
      tone: "within2",
      accessibleName: "Variance +0.4 percent, within 2 percent",
      reason: null,
    });
    expect(provisional?.explained.text).toBe("−$0.21");
    expect(provisional?.variance).toMatchObject({ text: "+7.1 %", tone: "within10" });
    expect(provisional?.variance.accessibleName).toBe(
      "Variance +7.1 percent, 2 to 10 percent band",
    );
  });

  it("carries each group with its models, token classes and explained lines", () => {
    const settled = rows[2];
    expect(settled?.meta).toBe("Settled · fetched Oct 4, 03:30 UTC · 1 model group");
    const [sonnet] = settled?.groups ?? [];
    expect(sonnet?.name).toBe("claude-sonnet-4-6");
    expect(sonnet?.models).toEqual([
      "Provider model claude-sonnet-4-6",
      "Spanlight model claude-sonnet-4-6-20260301",
    ]);
    expect(sonnet?.tokens.map((token) => [token.label, token.difference.text])).toEqual([
      ["Uncached input", "0"],
      ["Cache read", "0"],
      ["Cache write · 5 min", "+2"],
      ["Cache write · 1 h", "0"],
      ["Output", "−1"],
    ]);
    expect(sonnet?.lines).toEqual([
      {
        key: "server_tool_fees",
        label: "Server-tool fees",
        note: "10 web searches on the provider side, 0 recorded",
        usd: { text: "$0.10", reason: null },
      },
      {
        key: "usage_not_reported",
        label: "Usage not reported",
        note: "3 calls whose output tokens were never reported (2 client disconnects)",
        usd: {
          text: null,
          reason: "Counted, not priced: Spanlight knows the calls or tokens but has no USD figure.",
        },
      },
    ]);
  });

  it("keeps a negative priority commitment and lists credits and other charges apart", () => {
    const provisional = rows[1];
    const lines = provisional?.groups[0]?.lines ?? [];
    expect(lines[0]).toMatchObject({ label: "Priority commitment", usd: { text: "−$0.31" } });
    expect(provisional?.credits).toEqual([{ description: "Promotional credit", usd: "−$5.00" }]);
    expect(provisional?.other).toEqual([{ description: "Code Execution Usage", usd: "$0.02" }]);
    expect(provisional?.chargesKnown).toBe(true);
  });

  it("explains an empty day of a fetched source, and the dash of unpriced lines", () => {
    expect(rows[2]?.footnote).toBe(
      "No credits or other provider charges on this day. — means counted, not priced: Spanlight knows how many calls or tokens but has no USD figure, so their cost stays in the residual.",
    );
    // Nothing is known of the provider's charges on a day no fetch covers.
    expect(rows[0]?.chargesKnown).toBe(false);
    expect(rows[0]?.footnote).toBeNull();
  });
});

describe("renderRules", () => {
  it("shows the provisional chip on a provisional day only", () => {
    expect(renderRules(PROVISIONAL)).toEqual({
      provisionalChip: true,
      incompleteChip: false,
      retentionChip: false,
      unknownReason: null,
    });
    expect(renderRules(day()).provisionalChip).toBe(false);
  });

  it("gives the reason of a day no fetch covers yet", () => {
    expect(renderRules(NOT_FETCHED).unknownReason).toMatch(/not fetched yet/i);
  });

  it("marks a day whose rollups are pending as incomplete", () => {
    expect(renderRules(PENDING)).toEqual({
      provisionalChip: true,
      incompleteChip: true,
      retentionChip: false,
      unknownReason: expect.stringMatching(/rollup pending/i) as unknown,
    });
  });

  it("reads an incomplete day that is not pending as past retention, even with no groups", () => {
    const rules = renderRules(day({ incomplete: true, groups: [] }));
    expect(rules).toMatchObject({ incompleteChip: false, retentionChip: true });
    expect(rules.unknownReason).toMatch(/spans no longer retained/i);
  });

  it("shows both chips on a pending day that is also past retention", () => {
    const both = { ...PENDING, groups: RETENTION.groups };
    expect(renderRules(both)).toMatchObject({ incompleteChip: true, retentionChip: true });
    expect(flattenDays(report([both]), "p1")[0]?.chips.map((chip) => chip.label)).toEqual([
      "Provisional",
      "Rollup pending",
      "Spans no longer retained",
    ]);
  });

  it("shows the retention chip, not the incomplete one, past retention", () => {
    expect(renderRules(RETENTION)).toEqual({
      provisionalChip: false,
      incompleteChip: false,
      retentionChip: true,
      unknownReason: expect.stringMatching(/spans no longer retained/i) as unknown,
    });
  });
});

describe("visible notes of a day (the tile layout has no tooltips)", () => {
  it("spell out the provisional rule and why figures are missing", () => {
    const [notFetched, provisional, settled] = flattenDays(
      report([day(), PROVISIONAL, NOT_FETCHED]),
      "p1",
    );
    expect(notFetched?.notes).toEqual([
      "Provisional: provider data can lag; this day is final 48 h after it ends and after a fetch past that point.",
      "Not fetched yet: no successful fetch covers this day.",
    ]);
    expect(provisional?.notes).toEqual([
      "Provisional: provider data can lag; this day is final 48 h after it ends and after a fetch past that point.",
    ]);
    expect(settled?.notes).toEqual([]);
  });
});

describe("unknown figures", () => {
  it("say why each figure is missing", () => {
    const [retention, notFetched, pending] = flattenDays(
      report([RETENTION, PENDING, NOT_FETCHED]),
      "p1",
    ).sort((a, b) => a.day.localeCompare(b.day));
    expect(notFetched?.day).toBe("2026-10-03");
    expect(notFetched?.provider.reason).toMatch(/not fetched yet/i);
    expect(notFetched?.residual.reason).toMatch(/not fetched yet/i);
    expect(notFetched?.variance.reason).toMatch(/not fetched yet/i);
    expect(notFetched?.groups[0]?.tokens[0]?.provider.reason).toMatch(/not fetched yet/i);
    expect(pending?.spanlight.reason).toMatch(/rollup pending/i);
    expect(pending?.residual.reason).toMatch(/rollup pending/i);
    expect(retention?.spanlight.reason).toMatch(/spans no longer retained/i);
  });

  it("says when Spanlight recorded no call at all, rather than an unpriced one", () => {
    const untraced = group({
      spanlight_models: [],
      spanlight_usd: null,
      explained: [
        {
          kind: "untraced_traffic",
          usd: "0.9000",
          count: null,
          tokens: null,
          note: "no span with this model",
        },
      ],
    });
    const unpriced = group({ group: "acme-llm", spanlight_usd: null });
    const [onlyUntraced] = flattenDays(
      report([day({ spanlight_usd: null, groups: [untraced] })]),
      "p1",
    );
    expect(onlyUntraced?.spanlight.reason).toMatch(/no calls/i);
    expect(onlyUntraced?.groups[0]?.spanlight.reason).toMatch(/no calls/i);
    const [mixed] = flattenDays(
      report([day({ spanlight_usd: null, groups: [untraced, unpriced] })]),
      "p1",
    );
    expect(mixed?.spanlight.reason).toMatch(/no price/i);
    expect(mixed?.groups[1]?.spanlight.reason).toMatch(/no price/i);
  });

  it("says why a group has no variance when the provider billed nothing", () => {
    const [row] = flattenDays(
      report([
        day({
          groups: [
            group({
              provider_usd: "0",
              variance_ratio: null,
              note: "no provider line: wrong source scope, or usage billed under another account",
            }),
          ],
        }),
      ]),
      "p1",
    );
    expect(row?.groups[0]?.variance.reason).toMatch(/nothing to compare/i);
    expect(row?.groups[0]?.note).toBe(
      "no provider line: wrong source scope, or usage billed under another account",
    );
  });
});

describe("summaryTiles", () => {
  it("sums the known days and names the band of the overall variance", () => {
    const tiles = summaryTiles(report([day(), PROVISIONAL, NOT_FETCHED]));
    expect(tiles.map((tile) => [tile.label, tile.value, tile.detail])).toEqual([
      ["Provider USD", "$22.35", "2 of 3 days fetched"],
      ["Spanlight USD", "$24.01", "3 days; $21.70 on fetched days"],
      ["Explained", "−$0.11", "Fees, batch spend and priority calls Spanlight can name (signed)"],
      ["Residual", "+$0.76", "Over the 2 fetched days"],
      ["Settled days", "1 of 3", "1 within 2 % · largest 0.4 %"],
    ]);
    expect(tiles[3]?.badge).toMatchObject({ text: "+3.4 % · 2–10 %", tone: "within10" });
  });

  it("covers the whole window when every day was fetched", () => {
    const tiles = summaryTiles(report([day()]));
    expect(tiles[0]?.detail).toBe("3 days · Oct 1 – Oct 3");
    expect(tiles[1]?.detail).toBe("From stored spans, 2 projects");
    expect(tiles[3]?.detail).toBe("Provider − Spanlight − explained");
  });

  it("shows the reasons of a source never fetched", () => {
    const tiles = summaryTiles(
      report([NOT_FETCHED], {
        days: 1,
        settled_days: 0,
        days_within_2pct: 0,
        max_abs_variance_ratio: null,
      }),
    );
    expect(tiles.map((tile) => [tile.value, tile.detail])).toEqual([
      [null, "Not fetched yet"],
      ["$2.31", "From stored spans, 2 projects"],
      [null, "Needs provider data"],
      [null, "Provider − Spanlight − explained"],
      ["0 of 1", "No day is settled yet"],
    ]);
    expect(tiles[3]?.badge).toMatchObject({ text: "No provider data", tone: "unknown" });
    expect(tiles[0]?.reason).toMatch(/not fetched yet/i);
  });
});

describe("pendingDayRanges", () => {
  it("names the days without provider figures and the fetched days still provisional", () => {
    const ranges = pendingDayRanges(
      report([
        day({ day: "2026-10-10", provisional: true }),
        day({ day: "2026-10-11", provisional: true }),
        { ...NOT_FETCHED, day: "2026-10-12" },
        { ...NOT_FETCHED, day: "2026-10-13" },
        { ...NOT_FETCHED, day: "2026-10-14" },
      ]),
    );
    expect(ranges).toEqual({ unfetched: "Oct 12 – Oct 14", provisional: "Oct 10 – Oct 11" });
  });
});
