import { describe, expect, it } from "vitest";

import { activeTab, HUBS, hubForPath, NAV_ITEMS, showsHubTabs } from "./nav-items";

describe("NAV_ITEMS", () => {
  it("keeps every page's G-hotkey, each one unique", () => {
    const shortcuts = NAV_ITEMS.map((item) => item.shortcut);
    expect(new Set(shortcuts).size).toBe(shortcuts.length);
    expect([...shortcuts].sort()).toEqual(
      [
        "G O",
        "G T",
        "G S",
        "G U",
        "G D",
        "G V",
        "G R",
        "G A",
        "G Q",
        "G N",
        "G X",
        "G E",
        "G P",
        "G L",
        "G C",
        "G B",
        "G G",
        "G ,",
      ].sort(),
    );
  });
});

describe("HUBS", () => {
  it("lists eight hubs, five above the hairline and three below", () => {
    expect(HUBS.map((hub) => hub.label)).toEqual([
      "Overview",
      "Traces",
      "Monitor",
      "Evaluate",
      "Prompts",
      "Cost",
      "Gateway",
      "Settings",
    ]);
    expect(HUBS.map((hub) => hub.split)).toEqual([
      "top",
      "top",
      "top",
      "top",
      "top",
      "bottom",
      "bottom",
      "bottom",
    ]);
  });
});

describe("Cost hub", () => {
  it("has four tabs in order: Spend, Budgets, Reconciliation, Pricing", () => {
    const cost = HUBS.find((hub) => hub.id === "cost");
    expect(cost?.tabs.map((tab) => tab.label)).toEqual([
      "Spend",
      "Budgets",
      "Reconciliation",
      "Pricing",
    ]);
  });

  it("opens Reconciliation in Cost, not in Settings", () => {
    const tab = HUBS.find((hub) => hub.id === "cost")?.tabs.find(
      (t) => t.label === "Reconciliation",
    );
    expect(tab?.to).toBe("/$orgId/$projectId/reconciliation");
    expect(tab).not.toHaveProperty("external");
  });
});

describe("hubForPath and activeTab", () => {
  it.each([
    ["/o/p/traces/abc", "traces", "Traces"],
    ["/o/p/sessions/s1", "traces", "Sessions"],
    ["/o/p/users/u", "traces", "Users"],
    ["/o/p/doctor/i1", "monitor", "Doctor"],
    ["/o/p/alerts/r1", "monitor", "Alerts"],
    ["/o/p/annotate/q", "evaluate", "Review"],
    ["/o/p/queues/q", "evaluate", "Review"],
    ["/o/p/experiments/e/runs/r", "evaluate", "Experiments"],
    ["/o/p/playground", "prompts", "Playground"],
    ["/o/p/attribution", "cost", "Spend"],
    ["/o/p/pricing", "cost", "Pricing"],
    ["/o/p/reconciliation", "cost", "Reconciliation"],
    ["/o/p/settings/billing-sources/s1", "cost", "Reconciliation"],
    ["/o/p/settings/billing-sources", "settings", "Settings"],
    ["/o/p/gateway/keys", "gateway", "Keys"],
    ["/o/p/gateway", "gateway", "Overview"],
    ["/o/p/gateway/routes/r1", "gateway", "Routes"],
    ["/o/p/settings/channels", "settings", "Settings"],
    ["/o/p/overview", "overview", "Overview"],
  ])("%s is %s › %s", (pathname, hubId, tabLabel) => {
    const hub = hubForPath(pathname);
    expect(hub?.id).toBe(hubId);
    expect(hub && activeTab(hub, pathname)?.label).toBe(tabLabel);
  });

  it("returns null for a path no hub owns", () => {
    expect(hubForPath("/o/p/unknown")).toBeNull();
    expect(hubForPath("/account/security")).toBeNull();
  });
});

describe("showsHubTabs", () => {
  it.each([
    "/o/p/doctor",
    "/o/p/gateway/lab",
    "/o/p/budgets",
    "/o/p/pricing",
    "/o/p/reconciliation",
    "/o/p/gateway",
  ])("shows tabs on %s", (pathname) => {
    expect(showsHubTabs(pathname)).toBe(true);
  });

  it.each([
    "/o/p/overview",
    "/o/p/settings/project",
    "/o/p/traces/abc",
    "/o/p/experiments/new",
    "/o/p/gateway/routes/r1",
    "/o/p/settings/billing-sources",
    "/o/p/settings/billing-sources/s1",
  ])("shows breadcrumbs, not tabs, on %s", (pathname) => {
    expect(showsHubTabs(pathname)).toBe(false);
  });
});
