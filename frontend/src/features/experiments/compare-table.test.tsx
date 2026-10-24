import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ItemDelta } from "@/lib/api";

import { CompareTable } from "./compare-table";
import type { ScoreMeta } from "./score-meta";

const QUALITY: ScoreMeta = {
  name: "quality",
  kind: "numeric",
  evaluatorKind: null,
  categories: [],
};

function item(index: number, delta: number): ItemDelta {
  const side = (score: number, text: string) => ({
    trace_id: null,
    output: { text },
    scores: { quality: score },
    latency_ms: 800,
    cost_usd: "0.001",
    error: null,
  });
  return {
    dataset_item_id: `di_${String(index)}`,
    position: index + 1,
    input: { question: `Question ${String(index)}` },
    expected_output: null,
    changed: false,
    a: side(0.5, `Answer ${String(index)}`),
    b: side(0.5 + delta, `Answer ${String(index)} changed`),
    deltas: { quality: delta },
  };
}

// 40 items: 3 regressed, the rest improved, so the list pages at 20.
const ITEMS = Array.from({ length: 40 }, (_, index) => item(index, index < 3 ? -0.2 : 0.1));

function table(score: ScoreMeta) {
  return (
    <TooltipProvider>
      <CompareTable
        items={ITEMS}
        sort={{ scores: [QUALITY, score], score, onChange: () => undefined }}
        names={{ a: "Run #2", b: "Run #3" }}
        edited={0}
      />
    </TooltipProvider>
  );
}

function renderTable() {
  return render(table(QUALITY));
}

const rows = () =>
  within(screen.getByRole("list", { name: /Items sorted/ })).getAllByRole("listitem");

describe("CompareTable Regressions only", () => {
  it("keeps the regressed items, counts them once in the pinned bar and resets Show more", async () => {
    renderTable();
    await userEvent.click(screen.getByRole("button", { name: /Show 20 more/ }));
    expect(rows()).toHaveLength(40);

    await userEvent.click(screen.getByRole("switch", { name: "Regressions only" }));
    expect(rows()).toHaveLength(3);
    expect(screen.getAllByText("3 regressions of 40 items")).toHaveLength(1);
    expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("switch", { name: "Regressions only" }));
    expect(rows()).toHaveLength(20);
    expect(screen.queryByText("3 regressions of 40 items")).not.toBeInTheDocument();
  });

  // Final review T9: a categorical score has no deltas, so there is nothing to filter on.
  it("hides the switch and its count for a categorical score", async () => {
    const { rerender } = renderTable();
    await userEvent.click(screen.getByRole("switch", { name: "Regressions only" }));
    expect(rows()).toHaveLength(3);

    rerender(table({ ...QUALITY, name: "tone", kind: "categorical" }));
    expect(screen.queryByRole("switch", { name: "Regressions only" })).not.toBeInTheDocument();
    expect(screen.queryByText(/regressions? of/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No item regressed/)).not.toBeInTheDocument();
    expect(rows()).toHaveLength(20);
  });

  // Ruling 21 (WCAG 2.4.11): a focused control scrolls clear of the pinned bar and phone app bar.
  it("gives every focusable in an item row a scroll margin of the pinned bar", () => {
    renderTable();
    const [row] = rows();
    if (!row) throw new Error("no rows");
    expect(row).toHaveClass("**:scroll-mt-36", "lg:**:scroll-mt-24");
    expect(within(row).getByRole("button", { name: "Show diff" })).toBeInTheDocument();
  });
});

const ACCURACY: ScoreMeta = { ...QUALITY, name: "accuracy" };

/** The page keeps the selected score (`?score=`); this stands in for it. */
function SortableTable() {
  const [score, setScore] = useState(QUALITY);
  return (
    <TooltipProvider>
      <CompareTable
        items={ITEMS}
        sort={{
          scores: [QUALITY, ACCURACY],
          score,
          onChange: (name) => setScore(name === ACCURACY.name ? ACCURACY : QUALITY),
        }}
        names={{ a: "Run #2", b: "Run #3" }}
        edited={0}
      />
    </TooltipProvider>
  );
}

describe("CompareTable sort score", () => {
  // Code review: Show more carried over to the next score's list.
  it("goes back to the first page when the score changes", async () => {
    render(<SortableTable />);
    await userEvent.click(screen.getByRole("button", { name: /Show 20 more/ }));
    expect(rows()).toHaveLength(40);

    await userEvent.click(screen.getByRole("radio", { name: "accuracy" }));
    expect(rows()).toHaveLength(20);
  });

  // Code review: the pinned bar's count is the live region; the summary line would repeat it.
  it("keeps the list summary out of the live region", () => {
    renderTable();
    expect(screen.getByText(/^Sorted by change in quality/)).not.toHaveAttribute("aria-live");
  });
});
