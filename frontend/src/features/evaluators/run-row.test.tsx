import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { EvaluatorRun } from "@/lib/api";

import { RunRow } from "./run-row";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="/trace">{children}</a>,
}));

vi.mock("@/features/shell", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const COMMENT = "Cites KB-112 and matches the replacement policy.";

function run(comment: string | null): EvaluatorRun {
  return {
    id: "run_1",
    trace_id: "7f3a9c1e2b4d",
    span_id: null,
    status: "done",
    source: "online",
    cost_usd: "0.0011",
    reserved_usd: "0",
    error: null,
    started_at: "2026-10-21T10:00:00Z",
    heartbeat_at: null,
    finished_at: "2026-10-21T10:00:02Z",
    scores: [{ name: "relevance", value: 0.82, string_value: null, comment }],
  };
}

function renderRow(comment: string | null) {
  render(
    <TooltipProvider>
      <ul>
        <RunRow run={run(comment)} booleanScores={false} />
      </ul>
    </TooltipProvider>,
  );
}

describe("RunRow", () => {
  it("keeps the judge's reasoning behind a collapsed Reasoning button", async () => {
    renderRow(COMMENT);
    const button = screen.getByRole("button", { name: "Reasoning for relevance" });
    expect(button).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(button);
    expect(screen.getByText(COMMENT)).toBeVisible();
  });

  it("shows no Reasoning button when the judge gave no comment", () => {
    renderRow(null);
    expect(screen.queryByRole("button", { name: /Reasoning/ })).not.toBeInTheDocument();
  });
});
