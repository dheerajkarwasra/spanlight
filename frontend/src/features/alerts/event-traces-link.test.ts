import { describe, expect, it } from "vitest";

import type { AlertEvent } from "@/lib/api";

import { eventTracesLink, type TracesRuleView } from "./event-traces-link";

const NOW = new Date("2026-10-21T12:00:00Z");

function event(overrides: Partial<AlertEvent> = {}): AlertEvent {
  return {
    id: "ev_1",
    rule_id: "r1",
    rule_name: "Error rate > 5 %",
    rule_kind: "threshold",
    state: "resolved",
    value: "0.068",
    threshold: "0.05",
    started_at: "2026-10-21T10:00:00Z",
    resolved_at: "2026-10-21T10:40:00Z",
    acknowledged_by: null,
    acknowledged_at: null,
    ...overrides,
  };
}

function rule(overrides: Partial<TracesRuleView> = {}): TracesRuleView {
  return { metric: "p95_ms", window_minutes: 15, filters: {}, ...overrides };
}

describe("eventTracesLink", () => {
  it("opens traces for the window before the firing until it resolved", () => {
    expect(eventTracesLink(event(), rule(), NOW)).toEqual({
      to: "/$orgId/$projectId/traces",
      search: {
        range: "custom",
        from: "2026-10-21T09:45:00.000Z",
        to: "2026-10-21T10:40:00.000Z",
      },
      unsupported: [],
    });
  });

  it("runs to now while the event is still firing", () => {
    const link = eventTracesLink(event({ resolved_at: null, state: "firing" }), rule(), NOW);
    expect(link?.search.to).toBe("2026-10-21T12:00:00.000Z");
  });

  it("keeps a months-long firing inside the trace list's 90-day limit", () => {
    const link = eventTracesLink(
      event({ started_at: "2026-05-01T00:00:00Z", resolved_at: null, state: "firing" }),
      rule(),
      NOW,
    );
    expect(link?.search.to).toBe("2026-10-21T12:00:00.000Z");
    expect(link?.search.from).toBe("2026-07-23T12:00:00.000Z");
  });

  it("maps every filter the trace list can express", () => {
    const link = eventTracesLink(
      event(),
      rule({
        metric: "error_rate",
        filters: {
          model: "gpt-4.1",
          environment: "prod",
          error_class: "timeout",
          external_user_id: "u_42",
          trace_name: "checkout",
          metadata_key: "tenant",
          metadata_value: "acme",
        },
      }),
      NOW,
    );
    expect(link?.search).toMatchObject({
      model: "gpt-4.1",
      env: "prod",
      error_class: "timeout",
      status: "error",
      user: "u_42",
      name: "checkout",
      metadata_key: "tenant",
      metadata_value: "acme",
    });
    expect(link?.unsupported).toEqual([]);
  });

  it("filters to failed traces for the error count metric too", () => {
    expect(eventTracesLink(event(), rule({ metric: "error_calls" }), NOW)?.search.status).toBe(
      "error",
    );
    expect(eventTracesLink(event(), rule({ metric: "cost_usd" }), NOW)?.search).not.toHaveProperty(
      "status",
    );
  });

  it("names the filters the trace list can't express", () => {
    const link = eventTracesLink(
      event(),
      rule({
        metric: "error_rate",
        filters: { provider: "openai", rate_limit_kind: "tokens", source_key_id: "key_1" },
      }),
      NOW,
    );
    expect(link?.search).not.toHaveProperty("provider");
    expect(link?.unsupported).toEqual([
      "Traces can't filter by provider; showing all providers.",
      "Traces can't filter by rate-limit kind; showing every failed call.",
      "Traces can't filter by gateway key; showing all keys.",
    ]);
  });

  it("sends budget events to the budgets page", () => {
    expect(eventTracesLink(event({ rule_kind: "budget" }), undefined, NOW)).toEqual({
      to: "/$orgId/$projectId/budgets",
      search: {},
      unsupported: [],
    });
  });

  it("has no link while the rule isn't loaded", () => {
    expect(eventTracesLink(event(), undefined, NOW)).toBeNull();
  });
});
