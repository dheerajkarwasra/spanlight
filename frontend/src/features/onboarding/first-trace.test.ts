import { describe, expect, it } from "vitest";

import { firstTraceAction, firstTraceWindow, formatArrival } from "./first-trace";

describe("formatArrival", () => {
  it("formats the date and the 24-hour time", () => {
    const iso = "2026-10-08T14:32:07Z";
    const local = new Date(iso);
    const time = [local.getHours(), local.getMinutes(), local.getSeconds()]
      .map((part) => String(part).padStart(2, "0"))
      .join(":");
    expect(formatArrival(iso)).toMatch(new RegExp(`^[A-Z][a-z]{2} \\d{1,2}, \\d{4} at ${time}$`));
  });

  it("returns null for missing or invalid values", () => {
    expect(formatArrival(null)).toBeNull();
    expect(formatArrival(undefined)).toBeNull();
    expect(formatArrival("not a date")).toBeNull();
  });
});

describe("firstTraceWindow", () => {
  it("starts at the exact first trace time and ends one millisecond later", () => {
    expect(firstTraceWindow("2026-10-08T14:32:07.123456+00:00")).toEqual({
      from: "2026-10-08T14:32:07.123456+00:00",
      to: "2026-10-08T14:32:07.124Z",
    });
  });

  it("returns null for an unparseable time", () => {
    expect(firstTraceWindow("soon")).toBeNull();
  });
});

describe("firstTraceAction", () => {
  it.each([
    [false, true, "open"],
    [true, true, "offer"],
    [false, false, "offer"],
    [true, false, "offer"],
  ] as const)(
    "secret on screen %s, arrived this visit %s → %s",
    (secretOnScreen, arrivedThisVisit, action) => {
      expect(firstTraceAction({ secretOnScreen, arrivedThisVisit })).toBe(action);
    },
  );
});
