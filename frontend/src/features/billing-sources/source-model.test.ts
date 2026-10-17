import { describe, expect, it } from "vitest";

import type { BillingSource } from "@/lib/api";

import {
  compactAgo,
  formatUtc,
  lastSuccessLabel,
  projectsLabel,
  sourceMeta,
  statusView,
} from "./source-model";

const NOW = new Date("2026-10-16T12:00:00Z");

function source(overrides: Partial<BillingSource> = {}): BillingSource {
  return {
    id: "src_1",
    kind: "anthropic_admin_api",
    provider: "anthropic",
    name: "Prod account",
    provider_scope: null,
    enabled: true,
    status: "never_fetched",
    key_set: true,
    key_id: "v1",
    last_fetched_at: null,
    last_success_at: null,
    last_error: null,
    projects: [],
    created_by: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

describe("statusView", () => {
  it("says when an ok source was last fetched, with the exact time as the tooltip", () => {
    const view = statusView(source({ status: "ok", last_fetched_at: "2026-10-16T10:00:00Z" }), NOW);
    expect(view).toEqual({
      tone: "success",
      label: "OK · fetched 2 h ago",
      tooltip: "Last fetched Oct 16, 10:00 UTC",
      accessibleName: "Status: OK, fetched 2 h ago",
    });
  });

  it("says uploaded rather than fetched for a CSV source", () => {
    const view = statusView(
      source({ kind: "csv_upload", status: "ok", last_fetched_at: "2026-10-16T11:00:00Z" }),
      NOW,
    );
    expect(view.label).toBe("OK · uploaded 1 h ago");
    expect(view.tooltip).toBe("Last uploaded Oct 16, 11:00 UTC");
  });

  it("shows a failing source's status line in the chip and the tooltip", () => {
    const view = statusView(source({ status: "failing", last_error: "401 Unauthorized" }), NOW);
    expect(view).toEqual({
      tone: "danger",
      label: "Failing · 401 Unauthorized",
      tooltip: "The last fetch failed: 401 Unauthorized",
      accessibleName: "Status: Failing, 401 Unauthorized",
    });
  });

  it("explains a source that was never fetched, per kind", () => {
    expect(statusView(source(), NOW)).toEqual({
      tone: "neutral",
      label: "Never fetched",
      tooltip: "The first fetch runs at 03:30 UTC, or now with Refresh.",
      accessibleName: "Status: Never fetched",
    });
    expect(statusView(source({ kind: "csv_upload" }), NOW).tooltip).toBe(
      "Nothing uploaded yet. Upload the provider's cost export to compare it.",
    );
  });
});

describe("lastSuccessLabel", () => {
  it("gives the last success in UTC, or says there was none", () => {
    expect(lastSuccessLabel(source({ last_success_at: "2026-10-15T03:31:00Z" }))).toBe(
      "Last success Oct 15, 03:31 UTC",
    );
    expect(lastSuccessLabel(source())).toBe("No successful fetch yet");
    expect(lastSuccessLabel(source({ kind: "csv_upload" }))).toBe("No upload yet");
    expect(
      lastSuccessLabel(source({ kind: "csv_upload", last_success_at: "2026-10-15T03:31:00Z" })),
    ).toBe("Last upload Oct 15, 03:31 UTC");
  });
});

describe("labels", () => {
  it("names the kind and provider, and notes a source that is turned off", () => {
    expect(sourceMeta(source())).toBe("Admin API · anthropic");
    expect(sourceMeta(source({ kind: "csv_upload", provider: "google" }))).toBe(
      "CSV upload · google",
    );
    expect(sourceMeta(source({ enabled: false }))).toBe("Admin API · anthropic · turned off");
  });

  it("counts projects", () => {
    expect(projectsLabel(0)).toBe("0 projects");
    expect(projectsLabel(1)).toBe("1 project");
  });

  it("formats a timestamp in UTC", () => {
    expect(formatUtc("2026-10-05T23:59:00Z")).toBe("Oct 5, 23:59 UTC");
  });
});

describe("compactAgo", () => {
  it("fits the chip: just now, minutes, hours, then days", () => {
    expect(compactAgo("2026-10-16T11:59:30Z", NOW)).toBe("just now");
    expect(compactAgo("2026-10-16T11:58:00Z", NOW)).toBe("2 min ago");
    expect(compactAgo("2026-10-16T09:00:00Z", NOW)).toBe("3 h ago");
    expect(compactAgo("2026-10-13T12:00:00Z", NOW)).toBe("3 d ago");
  });
});
