import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  authApi,
  insightsApi,
  type Explanation,
  type InsightDetail,
  type PrecisionKind,
} from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PATH = `/${ORG_ID}/proj_1/doctor/ins_1`;

const EXPLANATION: Explanation = {
  id: "exp_1",
  model: "claude-sonnet",
  text: "Most failures are 429s.",
  cost_usd: "0.0100",
  created_by: null,
  created_at: "2026-10-21T14:05:00Z",
};

const INSIGHT: InsightDetail = {
  id: "ins_1",
  kind: "error_spike",
  label: "Error spike",
  severity: "critical",
  status: "open",
  title: "Error rate spiked on gpt-4.1-mini",
  summary: "219 of 1,204 calls failed.",
  failure_layer: "provider",
  certainty: "measured",
  evidence: {
    trace_ids: ["4f2a91c0aaaa", "1d7e3f42bbbb"],
    metrics: { error_rate: "0.1820", baseline_error_rate: "0.0210", calls: 1204, errors: 219 },
    window: { start: "2026-10-21T13:47:00Z", end: "2026-10-21T14:02:00Z" },
  },
  suggested_fix: "Add a fallback target.",
  verification: "The check resolves this finding after two clean runs.",
  first_seen_at: "2026-10-21T09:42:00Z",
  last_seen_at: "2026-10-21T14:02:00Z",
  occurrences: 6,
  resolved_at: null,
  muted_until: null,
  mute_reason: null,
  acknowledged_at: null,
  explanations: [EXPLANATION],
  evidence_spans: [
    {
      trace_id: "4f2a91c0aaaa",
      span_id: "span_9",
      name: "openai.chat.completions",
      status: "error",
      model: "gpt-4.1-mini",
    },
  ],
  feedback: { mine: null, right: 0, wrong: 0 },
};

const TOGGLE = /Explain with Claude.*explanation/;

const PRECISION: PrecisionKind = {
  kind: "error_spike",
  label: "Error spike",
  right: 18,
  wrong: 2,
  n: 20,
  precision: "0.9000",
  wilson_low: "0.6990",
  wilson_high: "0.9721",
  meets_target: true,
};

describe("InsightDetailPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(insightsApi, "get").mockResolvedValue(INSIGHT);
    vi.spyOn(insightsApi, "precision").mockResolvedValue({ kinds: [PRECISION] });
    vi.spyOn(insightsApi, "summary").mockResolvedValue({
      open_critical: 1,
      open_warning: 0,
      open_info: 0,
    });
  });

  it("leads with the observed value against its baseline as one labelled chart", async () => {
    renderApp(PATH);
    const evidence = await screen.findByRole("region", { name: "Evidence" });
    expect(
      within(evidence).getByRole("img", {
        name: "Error rate 18.2% vs baseline 2.1% from the 7 days before the last 24 hours · 15 min window",
      }),
    ).toBeInTheDocument();
    expect(within(evidence).getByText("Observed 18.2%")).toBeInTheDocument();
    expect(within(evidence).getByText("Baseline 2.1%")).toBeInTheDocument();
  });

  it("opens each example trace on its span, and says when a trace is gone", async () => {
    renderApp(PATH);
    const traces = await screen.findByRole("region", { name: "Example traces" });
    const link = within(traces).getByRole("link", { name: /4f2a91c0aaaa/ });
    expect(link).toHaveAttribute("href", expect.stringContaining("/traces/4f2a91c0aaaa"));
    expect(link).toHaveAttribute("href", expect.stringContaining("span=span_9"));
    expect(link).toHaveTextContent("openai.chat.completions");
    expect(link).toHaveTextContent("gpt-4.1-mini");
    expect(within(traces).getByText("Trace no longer stored")).toBeInTheDocument();
    expect(traces).toHaveTextContent("Each opens on its first failed span, or its first LLM span.");
    expect(within(traces).getAllByRole("link")).toHaveLength(1);
  });

  it("links an unpriced_spend finding to Pricing, prefilled with its provider and model", async () => {
    vi.spyOn(insightsApi, "get").mockResolvedValue({
      ...INSIGHT,
      kind: "unpriced_spend",
      label: "Unpriced spend",
      title: "ft:custom-v2 has usage but no price",
      summary:
        "96 calls to openai/ft:custom-v2 in 24 hours used 412 000 input and 38 100 output tokens with no price, so their cost shows as —.",
    });
    renderApp(PATH);

    const link = await screen.findByRole("link", { name: "Set a price for ft:custom-v2" });
    const href = new URL(link.getAttribute("href") ?? "", "http://x");
    expect(href.pathname).toBe("/acme/checkout/pricing");
    expect(href.searchParams.get("provider")).toBe("openai");
    expect(href.searchParams.get("model")).toBe("ft:custom-v2");
  });

  it("links to Pricing without a prefill when the finding names no model", async () => {
    vi.spyOn(insightsApi, "get").mockResolvedValue({
      ...INSIGHT,
      kind: "unpriced_spend",
      title: "— has usage but no price",
      summary: "20 calls to —/— in 24 hours used 1 input and 1 output tokens with no price.",
    });
    renderApp(PATH);

    const link = await screen.findByRole("link", { name: "Set prices" });
    expect(link.getAttribute("href")).toBe("/acme/checkout/pricing");
  });

  it("shows the check's track record", async () => {
    renderApp(PATH);
    expect(
      await screen.findByText("This check was right 18 of 20 times (precision 0.90, 0.70–0.97)"),
    ).toBeInTheDocument();
    expect(screen.getByText("Meets target")).toBeInTheDocument();
  });

  it("puts Explain last, collapsed, with the number of stored explanations", async () => {
    renderApp(PATH);
    const evidence = await screen.findByRole("region", { name: "Evidence" });
    const explain = screen.getByRole("region", { name: "Explain with Claude" });
    expect(
      evidence.compareDocumentPosition(explain) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    const toggle = within(explain).getByRole("button", { name: TOGGLE });
    expect(toggle).toHaveTextContent("1 explanation");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Most failures are 429s.")).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Most failures are 429s.")).toBeInTheDocument();
  });

  it("opens Explain when a fresh explanation arrives", async () => {
    let answer: (value: Explanation) => void = () => undefined;
    const explain = vi.spyOn(insightsApi, "explain").mockReturnValue(
      new Promise<Explanation>((resolve) => {
        answer = resolve;
      }),
    );
    renderApp(PATH);
    const region = await screen.findByRole("region", { name: "Explain with Claude" });
    const toggle = within(region).getByRole("button", { name: TOGGLE });
    // The ask itself lives inside the disclosure; open it, ask, close it, and the answer reopens it.
    await userEvent.click(toggle);
    await userEvent.click(within(region).getByRole("button", { name: "Explain again" }));
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(explain).toHaveBeenCalled();
    answer(EXPLANATION);
    await vi.waitFor(() => {
      expect(toggle).toHaveAttribute("aria-expanded", "true");
    });
  });
  it("hides the insight actions from a viewer and says why once", async () => {
    const membership = ME.memberships[0];
    if (membership === undefined) throw new Error("ME has no membership");
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      memberships: [{ ...membership, role: "viewer" }],
    });
    renderApp(PATH);

    expect(await screen.findByText(/^You have view-only access\./)).toBeInTheDocument();
    for (const name of [/^Mute/, "Resolve", "Acknowledge", "Unmute"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });
});
