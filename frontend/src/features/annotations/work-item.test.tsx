import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { QueueItem, TraceDetail } from "@/lib/api";

import { WorkItem } from "./work-item";

const trace = vi.hoisted(() => ({ data: null as TraceDetail | null }));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="/trace">{children}</a>,
}));
vi.mock("@/features/shell", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));
vi.mock("@/features/traces", () => ({
  useTraceQuery: () => ({ data: trace.data ?? undefined, isPending: false }),
  PayloadView: () => null,
}));

const ITEM = { id: "item_1", trace_id: "t1", span_id: null, status: "pending" } as QueueItem;

describe("WorkItem", () => {
  it("shows unreported token counts as unknown, not 0", () => {
    trace.data = {
      trace_id: "t1",
      name: "answer",
      models: [],
      spans: [],
      duration_ms: 12,
      input_tokens: null,
      output_tokens: 40,
      cost_usd: null,
      has_unpriced: false,
    } as unknown as TraceDetail;

    render(
      <TooltipProvider>
        <WorkItem item={ITEM} />
      </TooltipProvider>,
    );

    const tokens = screen.getByText("Tokens").parentElement as HTMLElement;
    expect(within(tokens).queryByText(/^0 →/)).not.toBeInTheDocument();
    expect(within(tokens).getByText(/→ 40/)).toBeInTheDocument();
    expect(within(tokens).getAllByLabelText(/^Unknown/)).toHaveLength(1);
  });
});
