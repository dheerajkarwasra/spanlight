import { describe, expect, it } from "vitest";

import {
  dayPartFor,
  errorCountFrom,
  firstNameOf,
  formatHeroDate,
  greetingFor,
  heroStatus,
  joinEyebrow,
  shortRangeLabel,
} from "./hero";

function at(hour: number, minute = 0): Date {
  return new Date(2026, 9, 8, hour, minute);
}

describe("dayPartFor", () => {
  it("splits the day at 05:00, 12:00 and 18:00", () => {
    expect(dayPartFor(at(4, 59))).toBe("evening");
    expect(dayPartFor(at(5))).toBe("morning");
    expect(dayPartFor(at(11, 59))).toBe("morning");
    expect(dayPartFor(at(12))).toBe("afternoon");
    expect(dayPartFor(at(17, 59))).toBe("afternoon");
    expect(dayPartFor(at(18))).toBe("evening");
    expect(dayPartFor(at(0))).toBe("evening");
  });
});

describe("greetingFor", () => {
  it("greets by first name", () => {
    expect(greetingFor(at(14), "Dheeraj Karwasra")).toBe("Good afternoon, Dheeraj.");
    expect(greetingFor(at(8), "  Ada  ")).toBe("Good morning, Ada.");
  });

  it("drops the name when there is none", () => {
    expect(greetingFor(at(20), "")).toBe("Good evening.");
    expect(greetingFor(at(20), null)).toBe("Good evening.");
  });

  it("takes the first word as the first name", () => {
    expect(firstNameOf("Grace Brewster Hopper")).toBe("Grace");
    expect(firstNameOf("   ")).toBeNull();
  });
});

describe("formatHeroDate", () => {
  it("reads weekday, day and month", () => {
    expect(formatHeroDate(at(9))).toBe("Thursday, 8 October");
  });
});

describe("joinEyebrow", () => {
  it("joins the present parts with a middle dot", () => {
    expect(
      joinEyebrow(["Thursday, 8 October", undefined, "production", null, "Last 24 hours"]),
    ).toBe("Thursday, 8 October · production · Last 24 hours");
  });
});

describe("shortRangeLabel", () => {
  it("gives a lower-case label for card headings", () => {
    expect(shortRangeLabel({ value: "24h" })).toBe("last 24h");
    expect(shortRangeLabel({ value: "1h" })).toBe("last hour");
    expect(shortRangeLabel({ value: "custom" })).toBe("selected range");
  });
});

describe("heroStatus", () => {
  it("waits for the first trace when the project never received one", () => {
    expect(heroStatus({ traces: 0, errors: 0, hasEverReceivedTraces: false })).toEqual({
      kind: "first-run",
      lead: "Waiting for your",
      accent: "first trace.",
    });
  });

  it("calls out an empty window when older traces exist (or that is unknown)", () => {
    expect(heroStatus({ traces: 0, errors: 0, hasEverReceivedTraces: true }).kind).toBe(
      "empty-window",
    );
    expect(heroStatus({ traces: 0, errors: 0, hasEverReceivedTraces: null }).accent).toBe(
      "this window.",
    );
  });

  it("asks for a look when calls failed", () => {
    expect(heroStatus({ traces: 50, errors: 1203, hasEverReceivedTraces: true })).toEqual({
      kind: "errors",
      lead: "1,203 errors",
      accent: "need a look.",
    });
    expect(heroStatus({ traces: 50, errors: 1, hasEverReceivedTraces: true })).toEqual({
      kind: "errors",
      lead: "1 error",
      accent: "needs a look.",
    });
  });

  it("adds open Doctor findings after the errors", () => {
    expect(
      heroStatus({ traces: 50, errors: 15, findings: 1, hasEverReceivedTraces: true }),
    ).toEqual({ kind: "errors", lead: "15 errors and 1 finding", accent: "need a look." });
    expect(
      heroStatus({ traces: 50, errors: 1, findings: 2, hasEverReceivedTraces: true }).lead,
    ).toBe("1 error and 2 findings");
  });

  it("asks for a look at open findings when no call failed", () => {
    expect(heroStatus({ traces: 50, errors: 0, findings: 1, hasEverReceivedTraces: true })).toEqual(
      {
        kind: "findings",
        lead: "1 finding",
        accent: "needs a look.",
      },
    );
    expect(heroStatus({ traces: 50, errors: 0, findings: 3, hasEverReceivedTraces: true })).toEqual(
      {
        kind: "findings",
        lead: "3 findings",
        accent: "need a look.",
      },
    );
  });

  it("says the models are behaving otherwise", () => {
    expect(heroStatus({ traces: 50, errors: 0, hasEverReceivedTraces: true })).toEqual({
      kind: "healthy",
      lead: "Your models are",
      accent: "behaving.",
    });
  });
});

describe("errorCountFrom", () => {
  it("recovers the count from the rate without floating-point noise", () => {
    expect(errorCountFrom(203 / 48_200, 48_200)).toBe(203);
    expect(errorCountFrom(0.1, 10)).toBe(1);
  });

  it("is unknown when the rate is", () => {
    expect(errorCountFrom(null, 0)).toBeNull();
  });
});
