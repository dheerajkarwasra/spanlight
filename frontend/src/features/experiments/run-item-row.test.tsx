import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ExperimentItem } from "@/lib/api";

import { ItemRow } from "./run-item-row";
import type { ScoreMeta } from "./score-meta";

const RELEVANCE: ScoreMeta = {
  name: "relevance",
  kind: "numeric",
  evaluatorKind: "llm_judge",
  categories: [],
};
const COMMENT = "The answer cites KB-112 and matches the replacement policy.";

function item(scoreComments: Record<string, string> | null): ExperimentItem {
  return {
    dataset_item_id: "di_1",
    trace_id: "trace_1",
    output: { text: "A free replacement ships today.", finish_reason: "stop" },
    scores: { relevance: 0.82 },
    score_comments: scoreComments,
    latency_ms: 900,
    cost_usd: "0.001",
    error: null,
    evaluator_error: null,
    input: { question: "My order arrived cracked." },
    expected_output: null,
    item_metadata: null,
    position: 12,
    created_at: "2026-10-21T10:00:00Z",
  };
}

function renderRow(scoreComments: Record<string, string> | null) {
  render(
    <TooltipProvider>
      <ul>
        <ItemRow item={item(scoreComments)} scores={[RELEVANCE]} />
      </ul>
    </TooltipProvider>,
  );
}

describe("ItemRow", () => {
  it("puts the judge's reasoning under the score, collapsed", async () => {
    renderRow({ relevance: COMMENT });
    const button = screen.getByRole("button", { name: "Reasoning for relevance" });
    expect(button).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(button);
    expect(screen.getByText(COMMENT)).toBeVisible();
  });

  it("shows no Reasoning button without a comment", () => {
    renderRow(null);
    expect(screen.queryByRole("button", { name: /Reasoning/ })).not.toBeInTheDocument();
  });
});
