import { describe, expect, it } from "vitest";

import type { CostLine, ExplanationStatus } from "@/lib/api";

import {
  formatExactUsd,
  formatLine,
  hasModelCall,
  outputBreakdownNote,
  recordedUsageSentence,
  reasonLabel,
  statusCopy,
  tierLabel,
  tierNoteCopy,
  varianceTone,
  visibleLines,
} from "./cost-explanation";

/** The lines of the spec §8.1 example response. */
const SPEC_LINES: CostLine[] = [
  {
    kind: "uncached_input",
    quantity: 12310,
    unit: "tokens",
    rate: "3.000000",
    rate_unit: "usd_per_mtok",
    multiplier: "1",
    usd: "0.03693000",
  },
  {
    kind: "cache_read",
    quantity: 48200,
    unit: "tokens",
    rate: "0.300000",
    rate_unit: "usd_per_mtok",
    multiplier: "1",
    usd: "0.01446000",
  },
  {
    kind: "cache_write_5m",
    quantity: 0,
    unit: "tokens",
    rate: "3.750000",
    rate_unit: "usd_per_mtok",
    multiplier: "1",
    usd: "0",
  },
  {
    kind: "cache_write_1h",
    quantity: 0,
    unit: "tokens",
    rate: "6.000000",
    rate_unit: "usd_per_mtok",
    multiplier: "1",
    usd: "0",
  },
  {
    kind: "output",
    quantity: 812,
    unit: "tokens",
    rate: "15.000000",
    rate_unit: "usd_per_mtok",
    multiplier: "1",
    usd: "0.01218000",
  },
];

function lineAt(index: number): CostLine {
  const line = SPEC_LINES[index];
  if (line === undefined) {
    throw new Error(`no line ${String(index)}`);
  }
  return line;
}

const FEE_LINE: CostLine = {
  kind: "web_search",
  quantity: 3,
  unit: "requests",
  rate: "10.00",
  rate_unit: "usd_per_1k_requests",
  multiplier: "1",
  usd: "0.03000000",
};

describe("statusCopy", () => {
  it.each<[ExplanationStatus, "ok" | "warning" | "muted", string]>([
    ["ok", "ok", "Matches"],
    ["mismatch", "warning", "Mismatch"],
    ["no_usage", "muted", "No usage"],
    ["no_price", "muted", "No price"],
    ["price_version_missing", "muted", "Price row gone"],
    ["override_deleted", "muted", "Price row gone"],
    ["cache_hit", "ok", "Cache hit"],
    ["usage_rollup", "muted", "Usage rollup"],
  ])("gives %s the %s tone and the chip %s", (status, tone, title) => {
    expect(statusCopy(status, "A sentence.")).toMatchObject({ tone, title });
  });

  it("passes the server's sentence through for the non-ok statuses", () => {
    const message =
      "Output tokens were not reported: the client disconnected, so the cost is unknown.";
    expect(statusCopy("no_usage", message).body).toBe(message);
    expect(statusCopy("override_deleted", "Gone.").body).toBe("Gone.");
  });

  it("says a mismatch is a bug, whatever the server sentence", () => {
    expect(statusCopy("mismatch", "Recomputed 1 differs from stored 2.").body).toBe(
      "This is a bug in Spanlight. Report it with the span id.",
    );
  });

  it("has no body for ok without a message", () => {
    expect(statusCopy("ok", null).body).toBeNull();
  });
});

describe("formatLine", () => {
  it("formats the spec §8.1 lines as the Figma rows", () => {
    expect(SPEC_LINES.map(formatLine)).toEqual([
      {
        kind: "Uncached input",
        quantity: "12,310",
        rate: "$3.00 / MTok",
        multiplier: "×1",
        usd: "$0.036930",
        dimmed: false,
      },
      {
        kind: "Cache read",
        quantity: "48,200",
        rate: "$0.30 / MTok",
        multiplier: "×1",
        usd: "$0.014460",
        dimmed: false,
      },
      {
        kind: "Cache write",
        quantity: "0",
        rate: "$3.75 / MTok",
        multiplier: "×1",
        usd: "$0",
        dimmed: true,
      },
      {
        kind: "Cache write · 1 h",
        quantity: "0",
        rate: "$6.00 / MTok",
        multiplier: "×1",
        usd: "$0",
        dimmed: true,
      },
      {
        kind: "Output",
        quantity: "812",
        rate: "$15.00 / MTok",
        multiplier: "×1",
        usd: "$0.012180",
        dimmed: false,
      },
    ]);
  });

  it("keeps a rate's significant digits past the cent", () => {
    expect(formatLine({ ...lineAt(1), rate: "0.075000" }).rate).toBe("$0.075 / MTok");
  });

  it("shows a batch multiplier without trailing zeros", () => {
    expect(formatLine({ ...lineAt(0), multiplier: "0.50" }).multiplier).toBe("×0.5");
  });

  it("prices a web search per thousand requests and has no tier multiplier", () => {
    expect(formatLine(FEE_LINE)).toEqual({
      kind: "Web search",
      quantity: "3",
      rate: "$10.00 / 1K requests",
      multiplier: null,
      usd: "$0.030000",
      dimmed: false,
    });
  });
});

describe("formatExactUsd", () => {
  it("shows six decimals, more when the amount has them, and never rounds", () => {
    expect(formatExactUsd("0.06357000")).toBe("$0.063570");
    expect(formatExactUsd("0.0000015")).toBe("$0.0000015");
    expect(formatExactUsd("1234.5")).toBe("$1,234.500000");
    expect(formatExactUsd("0")).toBe("$0.000000");
  });

  it("returns null for an unknown amount", () => {
    expect(formatExactUsd(null)).toBeNull();
  });
});

describe("visibleLines", () => {
  it("keeps the five token classes even when zero", () => {
    expect(visibleLines(SPEC_LINES)).toHaveLength(5);
  });

  it("drops a web search line with no searches: a $0 fee is noise", () => {
    expect(visibleLines([...SPEC_LINES, { ...FEE_LINE, quantity: 0, usd: "0" }])).toHaveLength(5);
    expect(visibleLines([...SPEC_LINES, FEE_LINE])).toHaveLength(6);
  });
});

describe("tierLabel", () => {
  it("names the tier, its multiplier and the note", () => {
    expect(tierLabel("standard", "1", null)).toBe("standard · ×1");
    expect(tierLabel("batch", "0.5", null)).toBe("batch · ×0.5");
    expect(tierLabel("priority", "1", "unmodelled")).toBe("priority · ×1 · unmodelled");
    expect(tierLabel("priority", "1", "commitment_billed")).toBe(
      "priority · ×1 · commitment-billed",
    );
  });

  it("is null when the tier was not reported", () => {
    expect(tierLabel(null, "1", null)).toBeNull();
  });
});

describe("tierNoteCopy", () => {
  it("explains Anthropic's commitment-billed priority", () => {
    expect(tierNoteCopy("commitment_billed", "anthropic", "priority")).toBe(
      "Priced at the standard rate. Anthropic bills priority under a capacity commitment, so these calls are not in the provider cost report; the reconciliation lists their standard-rate cost as a priority-commitment line.",
    );
  });

  it("explains OpenAI's unmodelled priority", () => {
    expect(tierNoteCopy("unmodelled", "openai", "priority")).toBe(
      "Priced at the standard rate. OpenAI bills priority (Fast) per model; the difference is counted as an unmodelled modifier in the reconciliation report.",
    );
  });

  it("names any other unmodelled tier plainly", () => {
    expect(tierNoteCopy("unmodelled", "google", "flex")).toBe(
      "Priced at the standard rate. Spanlight has no flex rate for google; the difference is counted as an unmodelled modifier in the reconciliation report.",
    );
  });

  it("is null without a note", () => {
    expect(tierNoteCopy(null, "openai", "standard")).toBeNull();
  });
});

describe("outputBreakdownNote", () => {
  it("says the reasoning tokens are part of the output", () => {
    expect(outputBreakdownNote({ reasoning: 300, other: 512 }, 812)).toBe(
      "Output 812 includes 300 reasoning tokens, billed at the output rate.",
    );
  });

  it("is null when the breakdown is unknown", () => {
    expect(outputBreakdownNote(null, 812)).toBeNull();
  });
});

describe("varianceTone", () => {
  it.each<[number | null, string]>([
    [0, "within2"],
    [0.02, "within2"],
    [-0.02, "within2"],
    [0.0201, "within10"],
    [0.1, "within10"],
    [-0.1, "within10"],
    [0.1001, "over"],
    [-0.5, "over"],
    [null, "unknown"],
    [Number.NaN, "unknown"],
  ])("puts %s in %s", (ratio, tone) => {
    expect(varianceTone(ratio)).toBe(tone);
  });
});

describe("recordedUsageSentence", () => {
  it("lists the usage that was recorded without a price", () => {
    expect(
      recordedUsageSentence({ input_tokens: 60510, cached_tokens: 48200, output_tokens: 812 }),
    ).toBe("Usage was recorded: 60,510 input, 48,200 cached, 812 output.");
  });

  it("leaves out an unreported cache count", () => {
    expect(recordedUsageSentence({ input_tokens: 10, cached_tokens: null, output_tokens: 2 })).toBe(
      "Usage was recorded: 10 input, 2 output.",
    );
  });

  it("is null when the usage itself is incomplete", () => {
    expect(
      recordedUsageSentence({ input_tokens: 10, cached_tokens: 0, output_tokens: null }),
    ).toBeNull();
  });
});

describe("reasonLabel", () => {
  it("turns the stored reason into words", () => {
    expect(reasonLabel("client_disconnected")).toBe("client disconnected");
    expect(reasonLabel("include_usage_not_set")).toBe("include usage not set");
  });

  it("is null when no reason was reported", () => {
    expect(reasonLabel(null)).toBeNull();
  });
});

describe("hasModelCall", () => {
  const none = {
    model: null,
    provider: null,
    input_tokens: null,
    output_tokens: null,
    cached_tokens: null,
    cache_write_tokens: null,
    cost_usd: null,
  };

  it("is false for a span with no model-call data", () => {
    expect(hasModelCall(none)).toBe(false);
  });

  it("is true once a model, a token count or a cost is known", () => {
    expect(hasModelCall({ ...none, model: "gpt-4.1" })).toBe(true);
    expect(hasModelCall({ ...none, input_tokens: 10 })).toBe(true);
    expect(hasModelCall({ ...none, cost_usd: "0.1" })).toBe(true);
  });
});
