import { describe, expect, it } from "vitest";

import type { Score, ScoreConfig } from "@/lib/api";

import { agreementFor, submittedValues, verdictText } from "./agreement";

function config(name: string, data_type: ScoreConfig["data_type"]): ScoreConfig {
  return {
    id: `cfg_${name}`,
    name,
    data_type,
    min_value: null,
    max_value: null,
    categories: [],
    description: null,
    implicit: false,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  };
}

function score(
  name: string,
  parts: Pick<Score, "value" | "string_value">,
  source: Score["source"] = "eval",
  created_at = "2026-10-21T10:00:00Z",
): Score {
  return {
    id: `${name}_${source}_${created_at}`,
    trace_id: "t1",
    span_id: null,
    name,
    data_type: "numeric",
    ...parts,
    source,
    comment: null,
    author_user_id: null,
    author_name: null,
    evaluator_run_id: null,
    created_at,
  };
}

const num = (value: number) => ({ value, string_value: null });
const cat = (string_value: string) => ({ value: null, string_value });

describe("agreementFor", () => {
  it("agrees on an equal yes/no score", () => {
    const [row] = agreementFor(
      [config("format", "boolean")],
      submittedValues([{ name: "format", value: true }]),
      [score("format", num(1))],
    );
    expect(row).toMatchObject({ verdict: "agree", difference: null });
  });

  it("disagrees on a different category", () => {
    const [row] = agreementFor([config("tone", "categorical")], { tone: cat("good") }, [
      score("tone", cat("bad")),
    ]);
    expect(row).toMatchObject({ you: cat("good"), evaluator: cat("bad"), verdict: "disagree" });
  });

  it("reads 4 against 3 as differs by 1, with no sign", () => {
    const [row] = agreementFor([config("helpfulness", "numeric")], { helpfulness: num(3) }, [
      score("helpfulness", num(4)),
    ]);
    expect(row).toMatchObject({ verdict: "differs", difference: 1 });
    expect(row && verdictText(row)).toBe("Differs by 1");
  });

  it("agrees on an equal number and keeps no tolerance", () => {
    const configs = [config("helpfulness", "numeric")];
    expect(
      agreementFor(configs, { helpfulness: num(4) }, [score("helpfulness", num(4))])[0],
    ).toMatchObject({ verdict: "agree" });
    const close = agreementFor(configs, { helpfulness: num(4) }, [score("helpfulness", num(4.1))]);
    expect(close[0] && verdictText(close[0])).toBe("Differs by 0.1");
  });

  it("counts only evaluator scores of the same name", () => {
    const [row] = agreementFor([config("helpfulness", "numeric")], { helpfulness: num(4) }, [
      score("helpfulness", num(1), "human"),
      score("helpfulness", num(2), "api"),
      score("accuracy", num(3)),
    ]);
    expect(row).toMatchObject({ evaluator: null, verdict: "none" });
    expect(row && verdictText(row)).toBe("No evaluator score");
  });

  it("compares the newest of several evaluator scores", () => {
    const [row] = agreementFor([config("helpfulness", "numeric")], { helpfulness: num(4) }, [
      // Older first, so taking the first score instead of the newest fails.
      score("helpfulness", num(2), "eval", "2026-10-21T09:00:00Z"),
      score("helpfulness", num(4), "eval", "2026-10-21T12:00:00Z"),
    ]);
    expect(row).toMatchObject({ evaluator: num(4), verdict: "agree" });
  });

  it("has no verdict when the reviewer did not score an optional config", () => {
    const [row] = agreementFor([config("tone", "categorical")], {}, [score("tone", cat("bad"))]);
    expect(row).toMatchObject({ you: null, verdict: "none", difference: null });
  });

  it("has one row per config in the queue's order", () => {
    const rows = agreementFor(
      [config("b", "numeric"), config("a", "boolean")],
      { a: num(1), b: num(2) },
      [],
    );
    expect(rows.map((row) => row.name)).toEqual(["b", "a"]);
  });
});
