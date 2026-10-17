import { describe, expect, it } from "vitest";

import type { BillingSource } from "@/lib/api";

import { failingCopy, neverFetchedCopy, sourceSummary, tableFootnote } from "./reconciliation-copy";

function source(overrides: Partial<BillingSource> = {}): BillingSource {
  return {
    id: "src_1",
    kind: "anthropic_admin_api",
    provider: "anthropic",
    name: "Prod account",
    provider_scope: null,
    enabled: true,
    status: "ok",
    key_set: true,
    key_id: "v1",
    last_fetched_at: "2026-10-15T03:31:00Z",
    last_success_at: "2026-10-15T03:31:00Z",
    last_error: null,
    projects: [
      { id: "p1", name: "Support Copilot", slug: "support-copilot" },
      { id: "p2", name: "Eval harness", slug: "eval-harness" },
    ],
    created_by: null,
    created_at: "2026-10-15T04:58:00Z",
    updated_at: "2026-10-15T04:58:00Z",
    ...overrides,
  };
}

describe("sourceSummary", () => {
  it("names the kind, the projects and the last success in UTC", () => {
    expect(sourceSummary(source())).toBe(
      "Anthropic Admin API · compares Support Copilot and Eval harness · last success Oct 15, 03:31 UTC",
    );
  });

  it("says when a source that never succeeded was added", () => {
    const fresh = source({
      last_success_at: null,
      projects: [{ id: "p2", name: "Eval harness", slug: "eval-harness" }],
    });
    expect(sourceSummary(fresh)).toBe(
      "Anthropic Admin API · compares Eval harness · added Oct 15, 04:58 UTC",
    );
  });
});

describe("tableFootnote", () => {
  it("names the report and when a day settles", () => {
    expect(tableFootnote(source())).toBe(
      "Provider figures come from the Anthropic cost report, fetched daily at 03:30 UTC. A day is provisional until 48 h after it ends and a fetch has run past that point; only settled days count toward “within 2 %”.",
    );
  });

  it("explains the dash on a source never fetched", () => {
    expect(tableFootnote(source({ status: "never_fetched" }))).toBe(
      "Provider figures come from the Anthropic cost report, fetched daily at 03:30 UTC for the last 7 days. — marks a figure Spanlight doesn’t have yet; hover or focus it for the reason.",
    );
  });
});

describe("callouts", () => {
  it("tells a never-fetched API source that Refresh fetches it now", () => {
    expect(neverFetchedCopy(source()).body).toContain("Refresh fetches it now");
    expect(neverFetchedCopy(source({ kind: "csv_upload" })).body).toContain(
      "Upload the provider’s cost CSV",
    );
  });

  it("offers a new key when the provider refused the old one, with the days left open", () => {
    const copy = failingCopy(
      source({
        provider: "openai",
        kind: "openai_costs_api",
        status: "failing",
        last_error: "401 Unauthorized",
        last_success_at: "2026-10-12T03:30:00Z",
      }),
      { unfetched: "Oct 12 – Oct 14", provisional: "Oct 10 – Oct 11" },
    );
    expect(copy).toEqual({
      title: "The last fetch failed: 401 Unauthorized",
      body: "OpenAI refused the admin key. Rotate the key to resume the daily fetch. Until a fetch succeeds, Oct 12 – Oct 14 have no provider figures and Oct 10 – Oct 11 stay provisional. The last success was Oct 12, 03:30 UTC.",
      rotate: true,
    });
  });

  it("names a single open day in the singular", () => {
    const copy = failingCopy(source({ status: "failing", last_error: "503 Service Unavailable" }), {
      unfetched: "Oct 15",
      provisional: "Oct 14",
    });
    expect(copy.body).toContain(
      "Until a fetch succeeds, Oct 15 has no provider figures and Oct 14 stays provisional.",
    );
  });

  it("asks for a new upload, not a refresh, on a CSV source", () => {
    const copy = failingCopy(
      source({ kind: "csv_upload", status: "failing", last_error: "invalid response" }),
      { unfetched: null, provisional: null },
    );
    expect(copy.body.startsWith("Upload the file again once it is fixed.")).toBe(true);
    expect(copy.rotate).toBe(false);
  });

  it("does not offer a new key for a provider outage", () => {
    const copy = failingCopy(source({ status: "failing", last_error: "503 Service Unavailable" }), {
      unfetched: null,
      provisional: null,
    });
    expect(copy.rotate).toBe(false);
    expect(copy.body).toBe(
      "Refresh tries again now, and the daily fetch retries at 03:30 UTC. The last success was Oct 15, 03:31 UTC.",
    );
  });
});
