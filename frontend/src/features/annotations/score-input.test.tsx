import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ScoreConfig } from "@/lib/api";

import { ScoreInput } from "./score-input";

const CATEGORIES = [
  "none",
  "wrong_answer",
  "hallucination",
  "ignored_instructions",
  "format",
  "refusal",
  "tool_misuse",
  "context_loss",
  "truncated",
  "too_slow",
  "other",
];

const CONFIG: ScoreConfig = {
  id: "sc1",
  name: "failure_category",
  data_type: "categorical",
  min_value: null,
  max_value: null,
  categories: CATEGORIES,
  description: null,
  implicit: false,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

describe("ScoreInput", () => {
  it("counts a long category list in the chip instead of wrapping it inside the pill", () => {
    render(
      <ScoreInput
        config={CONFIG}
        value={null}
        onChange={() => undefined}
        keyed
        onFocusWithin={() => undefined}
      />,
    );

    expect(screen.getByText("categorical · 11 values")).toBeInTheDocument();
    expect(screen.queryByText(/none, wrong_answer/)).not.toBeInTheDocument();
    // Every value stays on screen as an option.
    expect(screen.getAllByRole("button")).toHaveLength(CATEGORIES.length);
  });
});
