import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { alertsApi, type AlertEvent, type AlertRule } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PATH = `/${ORG_ID}/proj_1/alerts`;

const RULE: AlertRule = {
  id: "r1",
  project_id: "proj_1",
  name: "Error rate > 5 %",
  kind: "threshold",
  metric: "error_rate",
  comparator: "gt",
  threshold: "0.05",
  window_minutes: 15,
  baseline_windows: null,
  sensitivity: null,
  filters: { provider: "openai", model: "gpt-4.1" },
  cooldown_minutes: 15,
  enabled: true,
  channel_ids: [],
  muted_until: null,
  created_by: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  state: {
    state: "firing",
    since: "2026-10-21T10:00:00Z",
    last_value: "0.07",
    last_evaluated_at: null,
  },
};

function firing(id: string, startedAt: string, resolvedAt: string | null): AlertEvent {
  return {
    id,
    rule_id: RULE.id,
    rule_name: RULE.name,
    rule_kind: "threshold",
    state: resolvedAt === null ? "firing" : "resolved",
    value: "0.068",
    threshold: "0.05",
    started_at: startedAt,
    resolved_at: resolvedAt,
    acknowledged_by: null,
    acknowledged_at: null,
  };
}

const OPEN = firing("e2", "2026-10-21T10:00:00Z", null);
const HISTORY = [OPEN, firing("e1", "2026-10-20T08:00:00Z", "2026-10-20T08:30:00Z")];

function mockAlerts(open: AlertEvent[]) {
  vi.spyOn(alertsApi, "rules").mockResolvedValue([RULE]);
  vi.spyOn(alertsApi, "channels").mockResolvedValue([]);
  vi.spyOn(alertsApi, "events").mockImplementation((_projectId, query) =>
    Promise.resolve({ items: query?.state === "firing" ? open : HISTORY, next_cursor: null }),
  );
}

describe("AlertsPage firing banner and history", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
  });

  it("shows no banner while nothing fires", async () => {
    mockAlerts([]);
    renderApp(PATH);

    expect(await screen.findByRole("heading", { name: "Firing history" })).toBeInTheDocument();
    expect(screen.queryByText(/rules? firing/)).not.toBeInTheDocument();
  });

  it("announces firing rules politely and links to the first one", async () => {
    mockAlerts([OPEN]);
    renderApp(PATH);

    const banner = await screen.findByText(/1 rule firing/);
    const status = banner.closest('[role="status"]');
    expect(status).not.toBeNull();
    expect(status).toHaveTextContent(/1 rule firing · Error rate > 5 % since/);
    const link = within(status as HTMLElement).getByRole("link", { name: /Error rate > 5 %/ });
    expect(link).toHaveAttribute("href", "/acme/checkout/alerts/r1");
  });

  it("groups firings by rule and expands to each firing's trace link", async () => {
    mockAlerts([OPEN]);
    renderApp(PATH);

    const history = await screen.findByRole("region", { name: "Firing history" });
    const toggle = await within(history).findByRole("button", { name: /Error rate > 5 %/ });
    expect(toggle).toHaveTextContent("Fired 2 times");
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const links = within(history).getAllByRole("link", { name: /View traces/ });
    expect(links).toHaveLength(2);
    const href = links[1]?.getAttribute("href") ?? "";
    expect(href).toContain("range=custom");
    expect(href).toContain("model=gpt-4.1");
    expect(href).toContain("status=error");
    expect(
      within(history).getAllByText("Traces can't filter by provider; showing all providers."),
    ).toHaveLength(2);
  });
});
