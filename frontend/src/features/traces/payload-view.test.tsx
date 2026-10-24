import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { projectsApi, type Span } from "@/lib/api";

import { PayloadView } from "./payload-view";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, className }: { children: ReactNode; className?: string }) => (
    <a href="/settings/project" className={className}>
      {children}
    </a>
  ),
}));

let capturePayloads = true;

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
  useProjectQuery: () => ({ data: { capture_payloads: capturePayloads } }),
}));

function makeSpan(overrides: Partial<Span>): Span {
  return {
    span_id: "span_1",
    parent_span_id: null,
    kind: "chain",
    name: "answer_ticket",
    status: "ok",
    status_message: null,
    error_class: null,
    provider_error_type: null,
    provider_error_code: null,
    rate_limit_kind: null,
    started_at: "2026-10-21T10:00:00Z",
    ended_at: "2026-10-21T10:00:02Z",
    duration_ms: 2000,
    provider: null,
    model: null,
    input_tokens: null,
    output_tokens: null,
    cached_tokens: null,
    cache_write_tokens: null,
    cache_write_1h_tokens: null,
    service_tier: null,
    web_search_requests: null,
    cost_usd: null,
    fee_usd: null,
    pricing_version: null,
    time_to_first_token_ms: null,
    input: null,
    output: null,
    attributes: {},
    truncated: false,
    payload_omitted: false,
    finish_reason: null,
    prompt_version_id: null,
    usage_rollup: false,
    usage_state: "not_applicable",
    request_hash: null,
    ...overrides,
  };
}

const CHILD = makeSpan({
  span_id: "child_1",
  parent_span_id: "span_1",
  kind: "llm",
  name: "chat claude-sonnet-4-5",
  input: [{ role: "user", content: "Order #48213 arrived with a cracked screen." }],
});

function renderView(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  capturePayloads = true;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PayloadView — root I/O from a child span (D4)", () => {
  it("shows the first LLM child's input, names it, and selects it on click", async () => {
    const user = userEvent.setup();
    const onSelectSpan = vi.fn();
    renderView(
      <PayloadView
        label="Input"
        value={null}
        spanTruncated={false}
        fallback={{ candidates: [CHILD], traceId: "trace_1", onSelectSpan }}
      />,
    );

    expect(screen.getByText("From child span")).toBeInTheDocument();
    expect(screen.getByText("chat claude-sonnet-4-5")).toBeInTheDocument();
    expect(screen.getByText("Order #48213 arrived with a cracked screen.")).toBeInTheDocument();
    expect(screen.queryByText(/No input captured/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Select span chat claude-sonnet-4-5" }));
    expect(onSelectSpan).toHaveBeenCalledWith("child_1");
  });

  it("fetches an omitted child payload, with a skeleton meanwhile, never the empty note", async () => {
    const omitted = { ...CHILD, input: null, payload_omitted: true };
    const fetchSpan = vi.spyOn(projectsApi, "span").mockResolvedValue(CHILD);
    renderView(
      <PayloadView
        label="Input"
        value={null}
        spanTruncated={false}
        fallback={{ candidates: [omitted], traceId: "trace_1", onSelectSpan: vi.fn() }}
      />,
    );

    expect(screen.getByRole("status", { name: "Loading input" })).toBeInTheDocument();
    expect(screen.queryByText(/has no input/)).not.toBeInTheDocument();
    expect(
      await screen.findByText("Order #48213 arrived with a cracked screen."),
    ).toBeInTheDocument();
    expect(fetchSpan).toHaveBeenCalledWith("proj_1", "trace_1", "child_1");
  });

  it("moves to the next child when a fetched omitted payload turns out empty", async () => {
    const earlier = makeSpan({
      span_id: "child_early",
      kind: "llm",
      name: "chat plan",
      output: "Plan: replace the screen.",
    });
    const later = makeSpan({ span_id: "child_late", kind: "llm", payload_omitted: true });
    vi.spyOn(projectsApi, "span").mockResolvedValue({ ...later, payload_omitted: false });
    renderView(
      <PayloadView
        label="Output"
        value={null}
        spanTruncated={false}
        fallback={{ candidates: [later, earlier], traceId: "trace_1", onSelectSpan: vi.fn() }}
      />,
    );

    expect(await screen.findByText("Plan: replace the screen.")).toBeInTheDocument();
    expect(screen.getByText("chat plan")).toBeInTheDocument();
    expect(screen.queryByText(/Its children don't either/)).not.toBeInTheDocument();
  });

  it("says the children have none once every candidate turns out empty", async () => {
    const later = makeSpan({ span_id: "child_late", kind: "llm", payload_omitted: true });
    vi.spyOn(projectsApi, "span").mockResolvedValue({ ...later, payload_omitted: false });
    renderView(
      <PayloadView
        label="Output"
        value={null}
        spanTruncated={false}
        fallback={{ candidates: [later], traceId: "trace_1", onSelectSpan: vi.fn() }}
      />,
    );

    expect(
      await screen.findByText("This span has no output. Its children don't either."),
    ).toBeVisible();
  });
});

describe("PayloadView — empty copy (D15)", () => {
  it("says the span and its children have no input when capture is on", () => {
    renderView(
      <PayloadView
        label="Input"
        value={null}
        spanTruncated={false}
        fallback={{ candidates: [], traceId: "trace_1", onSelectSpan: vi.fn() }}
      />,
    );

    expect(screen.getByText("This span has no input. Its children don't either.")).toBeVisible();
    expect(screen.queryByText(/payload capture is off/)).not.toBeInTheDocument();
  });

  it("does not speak for children it was not given", () => {
    renderView(<PayloadView label="Output" value={null} spanTruncated={false} />);
    expect(screen.getByText("This span has no output.")).toBeVisible();
  });

  it("points to project settings when payload capture is off", () => {
    capturePayloads = false;
    renderView(
      <PayloadView
        label="Input"
        value={null}
        spanTruncated={false}
        fallback={{ candidates: [], traceId: "trace_1", onSelectSpan: vi.fn() }}
      />,
    );

    expect(screen.getByText(/payload capture is off/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "project settings" })).toBeInTheDocument();
  });
});

describe("PayloadView — payload guard (D6)", () => {
  const big = { documents: "x".repeat(70 * 1024) };

  it("shows the first 16 KB of a payload over 64 KB until Load all", async () => {
    const user = userEvent.setup();
    renderView(<PayloadView label="Output" value={big} spanTruncated={false} />);

    const footer = screen.getByText("Showing 16 KB of 70 KB").parentElement;
    expect(footer?.textContent).toBe("Showing 16 KB of 70 KB · Load all · Download");
    const preview = screen.getByRole("region", { name: "Output preview" });
    expect(preview.textContent.length).toBeLessThanOrEqual(16 * 1024);

    await user.click(screen.getByRole("button", { name: "Load all" }));
    expect(screen.queryByText(/Showing 16 KB/)).not.toBeInTheDocument();
    expect(screen.getByText('"documents"')).toBeInTheDocument();
  });

  it("downloads the full JSON through a Blob link", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn((_blob: Blob) => "blob:payload");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderView(<PayloadView label="Output" value={big} spanTruncated={false} />);

    await user.click(screen.getByRole("button", { name: "Download" }));

    expect(click).toHaveBeenCalledOnce();
    const blob = createObjectURL.mock.calls[0]?.[0];
    expect(blob).toBeInstanceOf(Blob);
    expect(JSON.parse(await (blob as Blob).text())).toEqual(big);
  });

  it("renders a payload under 64 KB as before", () => {
    renderView(<PayloadView label="Output" value={{ a: 1 }} spanTruncated={false} />);
    expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
    expect(screen.getByText('"a"')).toBeInTheDocument();
  });
});

describe("PayloadView — reasoning and images (D5, D6)", () => {
  it("renders thinking as a collapsed Reasoning card above the answer", async () => {
    const user = userEvent.setup();
    renderView(
      <PayloadView
        label="Output"
        value={{
          role: "assistant",
          content: [
            { type: "thinking", thinking: "KB-112 allows a free replacement." },
            { type: "text", text: "Replacement started." },
          ],
        }}
        spanTruncated={false}
      />,
    );

    const toggle = screen.getByRole("button", { name: "Reasoning" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("KB-112 allows a free replacement.")).not.toBeInTheDocument();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("KB-112 allows a free replacement.")).toBeVisible();
    expect(screen.getByText("Replacement started.")).toBeVisible();
  });

  it("says when the provider redacted the reasoning", () => {
    renderView(
      <PayloadView
        label="Output"
        value={{
          role: "assistant",
          content: [
            { type: "redacted_thinking", data: "opaque" },
            { type: "text", text: "Done." },
          ],
        }}
        spanTruncated={false}
      />,
    );
    expect(screen.getByText("Reasoning redacted by the provider")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Reasoning/ })).not.toBeInTheDocument();
  });

  it("shows an inline base64 image as a lazy thumbnail", () => {
    renderView(
      <PayloadView
        label="Input"
        value={[
          {
            role: "user",
            content: [
              { type: "image", source: { type: "base64", media_type: "image/png", data: "iVBO" } },
            ],
          },
        ]}
        spanTruncated={false}
      />,
    );
    const image = screen.getByRole("img", { name: "Inline image (image/png, base64)" });
    expect(image).toHaveAttribute("src", "data:image/png;base64,iVBO");
    expect(image).toHaveAttribute("loading", "lazy");
  });
});
