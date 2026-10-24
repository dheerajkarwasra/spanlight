import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  authApi,
  insightsApi,
  projectsApi,
  type Insight,
  type SessionTurn,
  type Span,
  type TraceSummary,
} from "@/lib/api";
import { testSpan } from "@/features/traces/span-test-data";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

function trace(index: number): TraceSummary {
  const start = new Date(Date.UTC(2026, 9, 7, 12, index)).toISOString();
  return {
    trace_id: `trace-${index}`,
    name: "answer_ticket",
    environment: null,
    release: null,
    external_user_id: null,
    session_id: "chat/1",
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

function llmSpan(index: number, payloadOmitted = false): Span {
  return {
    span_id: `span-${index}`,
    parent_span_id: null,
    kind: "llm",
    name: "chat",
    status: "ok",
    started_at: trace(index).started_at,
    input: payloadOmitted ? null : [{ role: "user", content: `Question ${index}` }],
    output: payloadOmitted ? null : `Answer ${index}`,
    payload_omitted: payloadOmitted,
  } as unknown as Span;
}

function turn(index: number, payloadOmitted = false): SessionTurn {
  return { trace: trace(index), spans: [llmSpan(index, payloadOmitted)], tool_calls: 0 };
}

describe("SessionDetailPage", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
  });

  it("reads every turn's messages from one turns request, with no request per turn", async () => {
    const turns = vi
      .spyOn(projectsApi, "sessionTurns")
      .mockResolvedValue({ items: [turn(2), turn(1), turn(0)], next_cursor: null });
    const traceDetail = vi.spyOn(projectsApi, "trace");
    renderApp(`/${ORG_ID}/proj_1/sessions/${encodeURIComponent("chat/1")}`);

    expect(await screen.findByText("Question 0")).toBeInTheDocument();
    expect(screen.getByText("Answer 2")).toBeInTheDocument();
    expect(turns).toHaveBeenCalledTimes(1);
    expect(turns).toHaveBeenCalledWith(
      "proj_1",
      "chat/1",
      expect.objectContaining({ cursor: null }),
    );
    expect(traceDetail).not.toHaveBeenCalled();
  });

  it("loads only the omitted spans of a turn whose payloads were over the page budget", async () => {
    const root: Span = {
      ...llmSpan(9),
      span_id: "root-1",
      kind: "agent",
      input: null,
      output: null,
    };
    vi.spyOn(projectsApi, "sessionTurns").mockResolvedValue({
      items: [{ trace: trace(1), spans: [root, llmSpan(1, true)], tool_calls: 0 }, turn(0)],
      next_cursor: null,
    });
    const traceDetail = vi
      .spyOn(projectsApi, "trace")
      .mockResolvedValue({ ...trace(1), spans: [root, llmSpan(1)] });
    const spanDetail = vi.spyOn(projectsApi, "span").mockResolvedValue(llmSpan(1));
    renderApp(`/${ORG_ID}/proj_1/sessions/chat_1`);

    expect(await screen.findByText("Question 1")).toBeInTheDocument();
    expect(screen.getByText("Answer 1")).toBeInTheDocument();
    expect(spanDetail).toHaveBeenCalledTimes(1);
    expect(spanDetail).toHaveBeenCalledWith("proj_1", "trace-1", "span-1");
    expect(traceDetail).not.toHaveBeenCalled();
  });
});

function withSystem(index: number, system: string): SessionTurn {
  const span = llmSpan(index);
  return {
    ...turn(index),
    spans: [{ ...span, input: [{ role: "system", content: system }, ...(span.input as object[])] }],
  };
}

describe("SessionDetailPage turns", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
  });

  it("shows a steps pill that expands into the trace's waterfall, a row opening the trace", async () => {
    vi.spyOn(projectsApi, "sessionTurns").mockResolvedValue({
      items: [{ ...turn(0), tool_calls: 1 }],
      next_cursor: null,
    });
    const traceDetail = vi.spyOn(projectsApi, "trace").mockResolvedValue({
      ...trace(0),
      spans: [testSpan("span-0", null, 0, 1200, { kind: "llm" })],
    });
    const router = renderApp(`/${ORG_ID}/proj_1/sessions/chat_1`);

    const pill = await screen.findByRole("button", { name: /^2 steps · 1 tool call · .+ · \$/ });
    expect(pill).toHaveAttribute("aria-expanded", "false");
    expect(traceDetail).not.toHaveBeenCalled();

    await userEvent.click(pill);
    const tree = await screen.findByRole("tree", { name: "Spans" });
    expect(traceDetail).toHaveBeenCalledWith("proj_1", "trace-0");
    await userEvent.click(within(tree).getByRole("treeitem"));
    await waitFor(() => {
      expect(router.state.location.pathname).toMatch(/\/traces\/trace-0$/);
    });
    expect(new URLSearchParams(router.state.location.searchStr).get("span")).toBe("span-0");
  });

  it("shows turn 1's system prompt and whether later turns changed it", async () => {
    vi.spyOn(projectsApi, "sessionTurns").mockResolvedValue({
      items: [
        withSystem(2, "Be brief.\nAlways cite."),
        withSystem(1, "Be brief."),
        withSystem(0, "Be brief."),
      ],
      next_cursor: null,
    });
    renderApp(`/${ORG_ID}/proj_1/sessions/chat_1`);

    const first = await screen.findByRole("article", { name: /^Turn 1/ });
    expect(within(first).getByText("Be brief.")).toBeInTheDocument();
    const second = screen.getByRole("article", { name: /^Turn 2/ });
    expect(within(second).getByText("System prompt unchanged")).toBeInTheDocument();
    const third = screen.getByRole("article", { name: /^Turn 3/ });
    await userEvent.click(within(third).getByRole("button", { name: "System prompt changed" }));
    const diff = await screen.findByRole("list", { name: "System prompt changes since turn 1" });
    expect(within(diff).getByText("Always cite.")).toBeInTheDocument();
  });

  it("links to an open context-growth finding that cites one of the session's traces", async () => {
    vi.spyOn(projectsApi, "sessionTurns").mockResolvedValue({
      items: [turn(1), turn(0)],
      next_cursor: null,
    });
    const insight = {
      id: "ins_1",
      kind: "context_growth",
      evidence: { trace_ids: ["trace-1"], metrics: {}, window: { start: "", end: "" } },
    } as unknown as Insight;
    const list = vi
      .spyOn(insightsApi, "list")
      .mockResolvedValue({ items: [insight], next_cursor: null });
    renderApp(`/${ORG_ID}/proj_1/sessions/chat_1`);

    const link = await screen.findByRole("link", { name: "Doctor: context growth" });
    expect(link).toHaveAttribute("href", expect.stringContaining("/doctor/ins_1"));
    expect(list).toHaveBeenCalledWith(
      "proj_1",
      expect.objectContaining({ kind: "context_growth", status: ["open"] }),
    );
  });

  it("offers Add to queue and Add to dataset, disabled for a viewer", async () => {
    vi.spyOn(projectsApi, "sessionTurns").mockResolvedValue({
      items: [turn(0)],
      next_cursor: null,
    });
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      memberships: ME.memberships.map((membership) => ({ ...membership, role: "viewer" })),
    });
    renderApp(`/${ORG_ID}/proj_1/sessions/chat_1`);

    expect(await screen.findByRole("button", { name: "Add to queue" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add to dataset" })).toBeDisabled();
  });

  it("says the actions add only the loaded turns while earlier turns are not loaded", async () => {
    vi.spyOn(projectsApi, "sessionTurns").mockResolvedValue({
      items: [turn(0)],
      next_cursor: "more",
    });
    renderApp(`/${ORG_ID}/proj_1/sessions/chat_1`);

    expect(
      await screen.findByRole("button", { name: "Add to queue · 1 loaded turn" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add to dataset · 1 loaded turn" }),
    ).toBeInTheDocument();
  });

  it("shows no Doctor link for a context-growth finding that cites other traces", async () => {
    vi.spyOn(projectsApi, "sessionTurns").mockResolvedValue({
      items: [turn(0)],
      next_cursor: null,
    });
    const insight = {
      id: "ins_2",
      kind: "context_growth",
      evidence: { trace_ids: ["someone-else"], metrics: {}, window: { start: "", end: "" } },
    } as unknown as Insight;
    const list = vi
      .spyOn(insightsApi, "list")
      .mockResolvedValue({ items: [insight], next_cursor: null });
    renderApp(`/${ORG_ID}/proj_1/sessions/chat_1`);

    await screen.findByText("Question 0");
    await waitFor(() => {
      expect(list).toHaveBeenCalled();
    });
    expect(screen.queryByRole("link", { name: "Doctor: context growth" })).not.toBeInTheDocument();
  });

  it("puts each turn's input tokens in reach of the keyboard, a zero-token turn drawing no bar", async () => {
    vi.spyOn(projectsApi, "sessionTurns").mockResolvedValue({
      items: [turn(1), { ...turn(0), trace: { ...trace(0), input_tokens: 0 } }],
      next_cursor: null,
    });
    renderApp(`/${ORG_ID}/proj_1/sessions/chat_1`);

    const bars = await screen.findByRole("list", { name: /^Input tokens per turn/ });
    const [first, second] = within(bars).getAllByRole("listitem");
    if (!first || !second) throw new Error("expected two bars");
    expect(first).toHaveAccessibleName("Turn 1 · 0 input tokens");
    expect((first.firstElementChild as HTMLElement).style.height).toBe("0px");
    act(() => {
      second.focus();
    });
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Turn 2 · 10 input tokens");
    expect(screen.getByText(/^Reached \$.+ by turn 2$/)).toBeInTheDocument();
  });
});
