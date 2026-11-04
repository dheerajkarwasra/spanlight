import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { projectsApi } from "@/lib/api";

import { SelectedSpanDetail } from "./selected-span-detail";
import { testSpan } from "./span-test-data";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));
vi.mock("./span-replays-tab", () => ({ SpanReplaysTab: () => null }));
vi.mock("./span-cost-card", () => ({ SpanCostCard: () => null }));
vi.mock("@/features/scores", () => ({
  ScoresPanel: () => null,
  SpanScores: () => null,
}));

const STORED = { "app.feature": "triage" };

function renderSelected(span: ReturnType<typeof testSpan>, tab = "attributes") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <SelectedSpanDetail
          span={span}
          tab={tab}
          onTabChange={vi.fn()}
          inTrace={{ traceId: "trace_1", childNodes: [], onSelectSpan: vi.fn() }}
        />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return client;
}

describe("SelectedSpanDetail", () => {
  it("fetches the whole span when only its attributes were left out, then shows them", async () => {
    const partial = testSpan("s1", null, 0, 10, { attributes: {}, attributes_omitted: true });
    const fetched = vi
      .spyOn(projectsApi, "span")
      .mockResolvedValue({ ...partial, attributes: STORED, attributes_omitted: false });
    renderSelected(partial);

    expect(screen.getByRole("status")).toHaveTextContent("Loading span content…");
    expect(await screen.findByText(/triage/)).toBeInTheDocument();
    expect(fetched).toHaveBeenCalledWith("proj_1", "trace_1", "s1", expect.anything());
    expect(fetched).toHaveBeenCalledOnce();
  });

  it("keeps loaded content when a background refetch fails", async () => {
    const partial = testSpan("s4", null, 0, 10, { attributes: {}, attributes_omitted: true });
    const fetched = vi
      .spyOn(projectsApi, "span")
      .mockResolvedValueOnce({ ...partial, attributes: STORED, attributes_omitted: false })
      .mockRejectedValue(new Error("offline"));
    const client = renderSelected(partial);
    expect(await screen.findByText(/triage/)).toBeInTheDocument();

    await client.refetchQueries();
    await waitFor(() => {
      expect(client.getQueryCache().getAll()[0]?.state.status).toBe("error");
    });

    expect(fetched).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/triage/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not fetch a complete span", async () => {
    const fetched = vi.spyOn(projectsApi, "span");
    renderSelected(testSpan("s2", null, 0, 10, { attributes: STORED }));

    expect(await screen.findByText(/triage/)).toBeInTheDocument();
    expect(fetched).not.toHaveBeenCalled();
  });

  it("shows the failure with Retry, keeping the header and tabs", async () => {
    const partial = testSpan("s3", null, 0, 10, {
      payload_omitted: true,
      attributes_omitted: true,
    });
    vi.spyOn(projectsApi, "span").mockRejectedValue(new Error("offline"));
    renderSelected(partial, "raw");

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Couldn’t load span content");
    });
    expect(screen.getByRole("heading", { name: "s3" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Raw JSON" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Copy JSON" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
