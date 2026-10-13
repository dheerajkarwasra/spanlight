import { describe, expect, it } from "vitest";

import type { ScoreConfig } from "@/lib/api";

import {
  checkScoreInput,
  configChipText,
  formatScoreValue,
  rangeText,
  toScoreParts,
} from "./score-format";

// The checks mirror `app/scores/validation.py`: a value the form accepts must be stored, and a
// value it refuses must be one the API refuses too.

function config(overrides: Partial<ScoreConfig>): ScoreConfig {
  return {
    id: "sc1",
    name: "quality",
    data_type: "numeric",
    min_value: null,
    max_value: null,
    categories: [],
    description: null,
    implicit: false,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

describe("formatScoreValue", () => {
  it("shows an unknown or unreadable value as an em dash, never 0", () => {
    expect(formatScoreValue({ value: null, string_value: null }, "numeric")).toBe("—");
    expect(formatScoreValue(toScoreParts({ value: "abc", string_value: null }), null)).toBe("—");
  });

  it("reads evaluator decimal strings as numbers with at most four decimals", () => {
    expect(formatScoreValue(toScoreParts({ value: "0.82350000", string_value: null }), null)).toBe(
      "0.8235",
    );
    expect(formatScoreValue({ value: 0, string_value: null }, "numeric")).toBe("0");
  });

  it("shows booleans as Yes/No and categories as written", () => {
    expect(formatScoreValue({ value: 1, string_value: null }, "boolean")).toBe("Yes");
    expect(formatScoreValue({ value: 0, string_value: null }, "boolean")).toBe("No");
    expect(formatScoreValue({ value: null, string_value: "good" }, "categorical")).toBe("good");
  });
});

describe("checkScoreInput: numeric", () => {
  it("refuses blank and non-numeric text instead of sending 0", () => {
    for (const typed of ["", "   ", "abc", "Infinity", "NaN"]) {
      expect(checkScoreInput(config({}), typed)).toEqual({ ok: false, error: "Enter a number." });
    }
  });

  it("sends the value rounded to the four decimals the API keeps", () => {
    expect(checkScoreInput(config({}), " 0.123456 ")).toEqual({
      ok: true,
      input: { value: 0.1235 },
    });
  });

  it("checks the range inclusively on the rounded value", () => {
    const unit = config({ min_value: 0, max_value: 1 });
    expect(checkScoreInput(unit, "1")).toMatchObject({ ok: true });
    expect(checkScoreInput(unit, "0")).toMatchObject({ ok: true });
    expect(checkScoreInput(unit, "1.0001")).toEqual({
      ok: false,
      error: "Enter a number from 0 to 1.",
    });
    expect(checkScoreInput(unit, "-0.0001")).toMatchObject({ ok: false });
  });

  it("names the one open end of a half-open range", () => {
    expect(checkScoreInput(config({ min_value: 1 }), "0")).toEqual({
      ok: false,
      error: "Enter a number of 1 or more.",
    });
    expect(checkScoreInput(config({ max_value: 5 }), "6")).toEqual({
      ok: false,
      error: "Enter a number of 5 or less.",
    });
  });

  it("keeps any number within the API's ±1e9 limit", () => {
    expect(checkScoreInput(config({}), "1000000000")).toMatchObject({ ok: true });
    expect(checkScoreInput(config({}), "-1000000001")).toMatchObject({ ok: false });
  });
});

describe("checkScoreInput: boolean and categorical", () => {
  it("accepts only the picked true/false", () => {
    const boolean = config({ data_type: "boolean" });
    expect(checkScoreInput(boolean, "true")).toEqual({ ok: true, input: { value: true } });
    expect(checkScoreInput(boolean, "false")).toEqual({ ok: true, input: { value: false } });
    expect(checkScoreInput(boolean, "1")).toMatchObject({ ok: false });
  });

  it("accepts only a listed category when the config has a list", () => {
    const listed = config({ data_type: "categorical", categories: ["good", "bad"] });
    expect(checkScoreInput(listed, "good")).toEqual({ ok: true, input: { string_value: "good" } });
    expect(checkScoreInput(listed, "Good")).toMatchObject({ ok: false });
  });

  it("takes free text up to 200 characters for an open list", () => {
    const open = config({ data_type: "categorical" });
    expect(checkScoreInput(open, "  fine  ")).toEqual({
      ok: true,
      input: { string_value: "fine" },
    });
    expect(checkScoreInput(open, "")).toMatchObject({ ok: false });
    expect(checkScoreInput(open, "x".repeat(201))).toMatchObject({ ok: false });
  });

  // Finding T8-03: the API counts characters (code points); the form counted UTF-16 units, so a
  // 150-emoji label the server stores was refused as "more than 200 characters".
  it("counts characters, not UTF-16 units, toward the 200 limit", () => {
    const open = config({ data_type: "categorical" });
    expect(checkScoreInput(open, "😀".repeat(200))).toMatchObject({ ok: true });
    expect(checkScoreInput(open, "😀".repeat(201))).toMatchObject({ ok: false });
  });
});

describe("config chips", () => {
  it("describes ranges and lists", () => {
    expect(rangeText(config({ min_value: 0, max_value: 1 }))).toBe("0 to 1");
    expect(rangeText(config({}))).toBe("any number");
    expect(configChipText(config({ data_type: "boolean" }))).toBe("yes or no");
    expect(configChipText(config({ data_type: "categorical", categories: ["a", "b"] }))).toBe(
      "categorical · a, b",
    );
  });
});
