import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { createQueryClient } from "@/app/query-client";
import { ingestQuotaApi, type IngestQuota } from "@/lib/api";

import { IngestQuotaCard } from "./ingest-quota-card";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const QUOTA: IngestQuota = {
  limits: {
    spans_per_second: { value: 1000, source: "default" },
    spans_per_day: { value: 1_000_000, source: "project" },
    bytes_per_day: { value: 5 * 1024 ** 3, source: "default" },
  },
  today: {
    day: "2026-11-02",
    spans: 48_213,
    bytes: 221_884_113,
    resets_at: "2026-11-03T00:00:00Z",
  },
  rejected_days: [],
};

describe("IngestQuotaCard", () => {
  it("holds each quota row as a dt and dd group, with its source badge and meter inside them", async () => {
    vi.spyOn(ingestQuotaApi, "get").mockResolvedValue(QUOTA);
    render(
      <QueryClientProvider client={createQueryClient()}>
        <IngestQuotaCard />
      </QueryClientProvider>,
    );

    const meters = await screen.findAllByRole("meter");
    expect(meters).toHaveLength(2);
    const list = document.querySelector("dl");
    expect(list).not.toBeNull();
    // axe `definition-list` and `dlitem`: a dl holds only div groups, and a group only dt and dd.
    const groups = [...(list?.children ?? [])];
    expect(groups.map((group) => group.tagName)).toEqual(["DIV", "DIV", "DIV"]);
    for (const group of groups) {
      const tags = [...group.children].map((child) => child.tagName);
      expect(tags.filter((tag) => tag !== "DT" && tag !== "DD")).toEqual([]);
    }
    for (const meter of meters) {
      expect(meter.closest("dd")).not.toBeNull();
    }
    expect(screen.getByText("Set for this project").closest("dt")).not.toBeNull();
  });
});
