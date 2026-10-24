import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { SessionTurn, TraceSummary } from "@/lib/api";

import { SessionConversation } from "./session-conversation";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, className }: { children: ReactNode; className?: string }) => (
    <a href="/trace" className={className}>
      {children}
    </a>
  ),
}));

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

function makeTrace(index: number): TraceSummary {
  const start = new Date(Date.UTC(2026, 9, 7, 12, index)).toISOString();
  return {
    trace_id: `trace-${index}`,
    name: "answer_ticket",
    environment: null,
    release: null,
    external_user_id: null,
    session_id: "chat_1",
    tags: [],
    started_at: start,
    ended_at: start,
    duration_ms: 1200,
    span_count: 2,
    error_count: 0,
    input_tokens: 10,
    output_tokens: 5,
    cost_usd: "0.001",
    has_unpriced: false,
    models: ["claude-sonnet-4-5"],
    error_message: null,
    has_scores: false,
    scores: [],
    error_class: null,
    retry_calls: 0,
    metadata: {},
    retry_cost_usd: "0",
  };
}

function renderConversation(count: number, hasEarlierTurns = false) {
  render(
    <TooltipProvider>
      <SessionConversation
        turns={Array.from({ length: count }, (_, index): SessionTurn => ({
          trace: makeTrace(index),
          spans: [],
          tool_calls: 0,
        }))}
        hasEarlierTurns={hasEarlierTurns}
        isLoadingEarlier={false}
        loadEarlierFailed={false}
        onLoadEarlier={() => undefined}
      />
    </TooltipProvider>,
  );
}

describe("SessionConversation", () => {
  it("reveals long sessions ten turns at a time", async () => {
    const user = userEvent.setup();
    renderConversation(12);

    expect(screen.getAllByRole("article")).toHaveLength(10);
    expect(screen.getByText("Turns 1–10 of 12")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show 2 more turns" }));

    expect(screen.getAllByRole("article")).toHaveLength(12);
    expect(screen.getByText("12 turns")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /more turn/ })).not.toBeInTheDocument();
  });

  it("numbers turns only when the whole session is loaded", () => {
    renderConversation(2);
    expect(screen.getByRole("article", { name: "Turn 1: answer_ticket" })).toBeInTheDocument();
  });

  it("offers to load earlier turns and leaves partial sessions unnumbered", () => {
    renderConversation(2, true);

    expect(screen.getByRole("button", { name: "Load earlier turns" })).toBeInTheDocument();
    expect(screen.getByText("Latest 2 turns")).toBeInTheDocument();
    expect(screen.queryByText(/^Turn 1$/)).not.toBeInTheDocument();
  });
});
