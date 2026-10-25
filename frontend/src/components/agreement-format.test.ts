import { describe, expect, it } from "vitest";

import type { AgreementSummary } from "@/lib/api";

import { badgeLabel, badgeText, bandText, formatKappa } from "./agreement-format";

function summary(overrides: Partial<AgreementSummary>): AgreementSummary {
  return {
    evaluator_id: "ev_1",
    score_name: "helpfulness",
    status: "ok",
    pairs: 48,
    kappa: 0.72,
    flagged: false,
    ...overrides,
  };
}

describe("bandText", () => {
  it("bands κ at 0.80 and 0.60", () => {
    expect(bandText(0.8)).toBe("Strong agreement");
    expect(bandText(0.7999)).toBe("Moderate agreement");
    expect(bandText(0.6)).toBe("Moderate agreement");
    expect(bandText(0.5999)).toBe("Weak agreement");
  });
});

describe("formatKappa", () => {
  it("truncates toward zero so the value never crosses into another band", () => {
    expect(formatKappa(0.5996)).toBe("0.59");
    expect(bandText(0.5996)).toBe("Weak agreement");
    expect(formatKappa(0.7999)).toBe("0.79");
    expect(bandText(0.7999)).toBe("Moderate agreement");
    expect(formatKappa(0.8)).toBe("0.80");
    expect(bandText(0.8)).toBe("Strong agreement");
    expect(formatKappa(0.29)).toBe("0.29");
    expect(formatKappa(-1)).toBe("-1.00");
    expect(formatKappa(-0.5996)).toBe("-0.59");
    expect(formatKappa(-0.004)).toBe("0.00");
    expect(formatKappa(null)).toBe("—");
  });

  it("shows the truncated value in the flagged badge", () => {
    expect(badgeText(summary({ kappa: 0.5996, flagged: true }))).toBe("κ 0.59 · weak");
  });
});

describe("badgeText", () => {
  it("names κ and the pairs when agreement is fine", () => {
    expect(badgeText(summary({}))).toBe("κ 0.72 · 48 pairs");
  });

  it("says weak when κ is flagged", () => {
    expect(badgeText(summary({ kappa: 0.41, flagged: true, pairs: 64 }))).toBe("κ 0.41 · weak");
  });

  it("says not calibrated with the count under 30 pairs", () => {
    expect(badgeText(summary({ status: "not_enough_pairs", pairs: 12, kappa: null }))).toBe(
      "Not calibrated · 12/30",
    );
  });

  it("shows a dash when κ is undefined", () => {
    expect(badgeText(summary({ status: "undefined", pairs: 41, kappa: null }))).toBe(
      "κ — · 41 pairs",
    );
  });

  it("has nothing to say for an unsupported score", () => {
    expect(badgeText(summary({ status: "unsupported", kappa: null }))).toBeNull();
  });
});

describe("badgeLabel", () => {
  it("is the full sentence", () => {
    expect(badgeLabel(summary({}))).toBe(
      "Judge agreement with reviewers: kappa 0.72 over 48 traces",
    );
    expect(badgeLabel(summary({ status: "not_enough_pairs", pairs: 12, kappa: null }))).toBe(
      "Judge agreement with reviewers: not calibrated, 12 of 30 paired traces",
    );
  });
});
