import { describe, expect, it } from "vitest";

import { REFUSED_TOOLTIP, refusedSentences, type RefusedDay } from "./refused-spans-copy";

function day(day: string, spans: number, rate_spans = 0): RefusedDay {
  return { day, spans, rate_spans };
}

describe("refusedSentences", () => {
  it("is empty when nothing was refused", () => {
    expect(refusedSentences([])).toEqual([]);
  });

  it("names the daily quota and the day, and warns that totals may be incomplete", () => {
    expect(refusedSentences([day("2026-11-01", 12_034)])).toEqual([
      "12,034 spans were refused by this project's daily ingest quota on 1 Nov. The totals for this range may be incomplete.",
    ]);
  });

  it("joins two adjacent days with a dash, two others with 'and', and counts three or more", () => {
    expect(refusedSentences([day("2026-11-01", 12_034), day("2026-11-05", 6)])[0]).toContain(
      "on 1 and 5 Nov.",
    );
    expect(refusedSentences([day("2026-10-30", 1), day("2026-11-01", 1)])[0]).toContain(
      "on 30 Oct and 1 Nov.",
    );
    expect(refusedSentences([day("2026-11-01", 12_034), day("2026-11-02", 6)])[0]).toContain(
      "on 1–2 Nov.",
    );
    expect(refusedSentences([day("2026-10-31", 1), day("2026-11-01", 1)])[0]).toContain(
      "on 31 Oct–1 Nov.",
    );
    expect(
      refusedSentences([day("2026-11-01", 1), day("2026-11-02", 1), day("2026-11-03", 1)])[0],
    ).toContain("on 3 days.");
  });

  it("says rate refusals were probably resent, and never calls anything lost", () => {
    const sentences = refusedSentences([day("2026-11-01", 3_210, 3_210)]);

    expect(sentences).toEqual([
      "3,210 spans were refused by this project's ingest rate limit on 1 Nov; clients that retry may have sent them again.",
    ]);
    expect(sentences.join(" ")).not.toMatch(/lost|dropped/);
  });

  it("puts the daily sentence before the rate sentence when both happened", () => {
    const sentences = refusedSentences([
      day("2026-11-01", 15_244, 3_210),
      day("2026-11-02", 500, 500),
    ]);

    expect(sentences).toHaveLength(2);
    expect(sentences[0]).toMatch(
      /^12,034 spans were refused by this project's daily ingest quota on 1 Nov\./,
    );
    expect(sentences[1]).toMatch(
      /^3,710 spans were refused by this project's ingest rate limit on 1–2 Nov;/,
    );
  });

  it("explains the retry over-count in the tooltip without the word lost", () => {
    expect(REFUSED_TOOLTIP).toMatch(/counted once per refusal/);
    expect(REFUSED_TOOLTIP).not.toMatch(/lost/);
  });
});
