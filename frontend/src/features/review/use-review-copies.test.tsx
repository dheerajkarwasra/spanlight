import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { useReviewCopies } from "./use-review-copies";

const updateItem = vi.hoisted(() => vi.fn(() => Promise.resolve({})));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  datasetsApi: { updateItem },
}));
vi.mock("@/features/shell", () => ({ useProjectParams: () => ({ projectId: "proj_1" }) }));

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

const metadata = {
  trace_id: "t1",
  span_id: "s1",
  review: { queue_id: "q1", item_id: "qi1", reason: "manual", failure_category: "other" },
};

describe("useReviewCopies", () => {
  // G-004: the copy saved before Submit carried the draft label, not the submitted one.
  it("rewrites a saved copy's failure category with the label the reviewer submits", async () => {
    const { result } = renderHook(() => useReviewCopies(), { wrapper });
    result.current.record("qi1", { datasetId: "ds1", itemId: "di1", metadata });
    await act(() =>
      result.current.settle("qi1", "complete", [
        { name: "failure_category", string_value: "wrong_tool" },
      ]),
    );
    expect(updateItem).toHaveBeenCalledWith("proj_1", "ds1", "di1", {
      metadata: { ...metadata, review: { ...metadata.review, failure_category: "wrong_tool" } },
    });
  });
});
