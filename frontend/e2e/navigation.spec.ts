import type { Page } from "@playwright/test";

import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { checkAtWidths, createWorkspace, expect, sendTrace, test as base } from "./workspace";

/*
 * The navigation shell (UX Phase 2): the 8 hubs and their tabs, every old deep link and G-hotkey
 * (W11), the alert channels redirect, the `?` overlay, the icon rail and collapse toggle, the
 * header contract's disabled controls, the 375 px layout, and axe with the WCAG 2.2 tags.
 */

interface NavWorkspace {
  base: string;
  traceId: string;
}

/** One workspace (with one trace) for the whole spec: navigation only reads it. */
const test = base.extend<object, { workspace: NavWorkspace }>({
  workspace: [
    async ({ browser, accountState }, provide, workerInfo) => {
      const context = await browser.newContext({
        baseURL: workerInfo.project.use.baseURL,
        storageState: accountState,
      });
      const page = await context.newPage();
      const { base, apiKey } = await createWorkspace(page, { apiKey: true });
      // A minute in the past: a container clock a little behind the browser's still accepts it.
      const traceId = await sendTrace(page, apiKey ?? "", {
        name: "nav-e2e",
        endTime: new Date(Date.now() - 60_000),
      });
      await context.close();
      await provide({ base, traceId });
    },
    { scope: "worker", timeout: 120_000 },
  ],
});

test.skip(!e2eEnabled, SKIP_REASON);
test.describe.configure({ timeout: 240_000 });

/** Each hub as the rail names it, and its tabs: label and path after `/{org}/{project}/`. */
const HUBS: { hub: string; tabs: { label: string; path: string }[] }[] = [
  { hub: "Overview", tabs: [{ label: "Overview", path: "overview" }] },
  {
    hub: "Traces",
    tabs: [
      { label: "Traces", path: "traces" },
      { label: "Sessions", path: "sessions" },
      { label: "Users", path: "users" },
    ],
  },
  {
    hub: "Monitor",
    tabs: [
      { label: "Doctor", path: "doctor" },
      { label: "Provider health", path: "providers" },
      { label: "Releases", path: "releases" },
      { label: "Alerts", path: "alerts" },
    ],
  },
  {
    hub: "Evaluate",
    tabs: [
      { label: "Review", path: "queues" },
      { label: "Datasets", path: "datasets" },
      { label: "Experiments", path: "experiments" },
      { label: "Evaluators", path: "evaluators" },
    ],
  },
  {
    hub: "Prompts",
    tabs: [
      { label: "Prompts", path: "prompts" },
      { label: "Playground", path: "playground" },
    ],
  },
  {
    hub: "Cost",
    tabs: [
      { label: "Spend", path: "attribution" },
      { label: "Budgets", path: "budgets" },
      { label: "Reconciliation", path: "reconciliation" },
      { label: "Pricing", path: "pricing" },
    ],
  },
  {
    hub: "Gateway",
    tabs: [
      { label: "Overview", path: "gateway" },
      { label: "Keys", path: "gateway/keys" },
      { label: "Routes", path: "gateway/routes" },
      { label: "Credentials", path: "gateway/credentials" },
      { label: "Lab", path: "gateway/lab" },
    ],
  },
  // The Settings link opens its first page.
  { hub: "Settings", tabs: [{ label: "Settings", path: "settings/project" }] },
];

/** Every settings page by its path segment, and its label in the Settings nav. */
const SETTINGS_PAGES: Record<string, string> = {
  project: "Project",
  keys: "API keys",
  exports: "Exports",
  organization: "Organization",
  members: "Members",
  channels: "Notification channels",
  "billing-sources": "Billing sources",
  audit: "Audit log",
  account: "Account",
  security: "Security",
  tokens: "Tokens",
};

/** Every pre-Phase-2 URL (W11): the page heading it shows and the hub it lights. */
const OLD_PATHS: { path: string; heading: string | RegExp; hub: string; settings?: string }[] = [
  { path: "overview", heading: /^Good (morning|afternoon|evening)/, hub: "Overview" },
  { path: "traces", heading: "Traces", hub: "Traces" },
  { path: "sessions", heading: "Sessions", hub: "Traces" },
  { path: "users", heading: "Users", hub: "Traces" },
  { path: "doctor", heading: "Doctor", hub: "Monitor" },
  { path: "providers", heading: "Provider health", hub: "Monitor" },
  { path: "releases", heading: "Compare releases", hub: "Monitor" },
  { path: "alerts", heading: "Alerts", hub: "Monitor" },
  { path: "budgets", heading: "Budgets", hub: "Cost" },
  { path: "attribution", heading: "Spend", hub: "Cost" },
  { path: "gateway", heading: "Gateway", hub: "Gateway" },
  { path: "gateway/keys", heading: "Gateway", hub: "Gateway" },
  { path: "gateway/routes", heading: "Gateway", hub: "Gateway" },
  { path: "gateway/credentials", heading: "Gateway", hub: "Gateway" },
  { path: "gateway/lab", heading: "Gateway", hub: "Gateway" },
  { path: "prompts", heading: "Prompts", hub: "Prompts" },
  { path: "playground", heading: "Playground", hub: "Prompts" },
  { path: "datasets", heading: "Datasets", hub: "Evaluate" },
  { path: "queues", heading: "Review", hub: "Evaluate" },
  { path: "evaluators", heading: "Evaluators", hub: "Evaluate" },
  { path: "experiments", heading: "Experiments", hub: "Evaluate" },
  // `settings/channels` is new in Phase 2 (it was /alerts/channels): the redirect test covers it.
  ...Object.entries(SETTINGS_PAGES)
    .filter(([page]) => page !== "channels")
    .map(([page, label]) => ({
      path: `settings/${page}`,
      heading: "Settings",
      hub: "Settings",
      settings: label,
    })),
];

/** Every G-hotkey and the page it opens; the test starts on Users, so none is a no-op. */
const HOTKEYS: [key: string, path: string][] = [
  ["o", "overview"],
  ["t", "traces"],
  ["s", "sessions"],
  ["u", "users"],
  ["d", "doctor"],
  ["v", "providers"],
  ["r", "releases"],
  ["a", "alerts"],
  ["q", "queues"],
  ["n", "datasets"],
  ["x", "experiments"],
  ["e", "evaluators"],
  ["p", "prompts"],
  ["l", "playground"],
  ["c", "attribution"],
  ["b", "budgets"],
  ["g", "gateway"],
  [",", "settings/project"],
];

function atPath(base: string, path: string): RegExp {
  return new RegExp(`${base}/${path}(\\?.*)?$`);
}

function rail(page: Page) {
  return page.getByRole("navigation", { name: "Main" });
}

async function expectCurrentHub(page: Page, hub: string) {
  await expect(rail(page).locator('[aria-current="page"]')).toHaveCount(1);
  await expect(rail(page).getByRole("link", { name: hub, exact: true })).toHaveAttribute(
    "aria-current",
    "page",
  );
}

async function hasNoHorizontalScroll(page: Page): Promise<boolean> {
  return page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  );
}

test("the rail fits 8 hubs in a 900 px window without scrolling", async ({ page, workspace }) => {
  for (const width of [1440, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${workspace.base}/overview`);
    await expect(rail(page).getByRole("link")).toHaveCount(8);
    const scroller = page.locator("[data-rail-scroller]");
    const fits = await scroller.evaluate((element) => element.scrollHeight <= element.clientHeight);
    expect(fits, `the rail scrolls at ${String(width)}×900`).toBe(true);
  }
});

test("every hub and every hub tab opens its page and lights the hub", async ({
  page,
  workspace,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${workspace.base}/overview`);
  for (const { hub, tabs } of HUBS) {
    await rail(page).getByRole("link", { name: hub, exact: true }).click();
    await expect(page).toHaveURL(atPath(workspace.base, tabs[0]?.path ?? ""));
    await expectCurrentHub(page, hub);
    if (tabs.length < 2) {
      // One-page hubs have no tab row.
      await expect(page.getByRole("navigation", { name: `${hub} sections` })).toHaveCount(0);
      continue;
    }
    for (const tab of tabs) {
      const row = page.getByRole("navigation", { name: `${hub} sections` });
      await row.getByRole("link", { name: tab.label, exact: true }).click();
      await expect(page).toHaveURL(atPath(workspace.base, tab.path));
      await expectCurrentHub(page, hub);
      await expect(row.getByRole("link", { name: tab.label, exact: true })).toHaveAttribute(
        "aria-current",
        "page",
      );
      await expect(row.locator('[aria-current="page"]')).toHaveCount(1);
    }
  }
});

test("every old deep link loads its page and lights its hub (W11)", async ({ page, workspace }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  for (const { path, heading, hub, settings } of OLD_PATHS) {
    await page.goto(`${workspace.base}/${path}`);
    await expect(page).toHaveURL(atPath(workspace.base, path));
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expectCurrentHub(page, hub);
    if (settings !== undefined) {
      const settingsNav = page.getByRole("navigation", { name: "Settings" });
      await expect(settingsNav.locator('[aria-current="page"]')).toHaveCount(1);
      await expect(settingsNav.getByRole("link", { name: settings, exact: true })).toHaveAttribute(
        "aria-current",
        "page",
      );
    }
  }
});

test("every G-hotkey opens its page", async ({ page, workspace }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${workspace.base}/users`);
  for (const [key, path] of HOTKEYS) {
    await expect(rail(page)).toBeVisible();
    // A page may focus its search field on load, and typing there must not navigate.
    await page.evaluate(() => {
      (document.activeElement as HTMLElement | null)?.blur();
    });
    await page.keyboard.press("g");
    await page.keyboard.press(key);
    await expect(page, `G ${key.toUpperCase()}`).toHaveURL(atPath(workspace.base, path));
  }
});

test("/alerts/channels redirects to Settings › Notification channels", async ({
  page,
  workspace,
}) => {
  await page.goto(`${workspace.base}/alerts/channels?x=1`);
  await expect(page).toHaveURL(atPath(workspace.base, "settings/channels"));
  await expect(page).toHaveURL(/[?&]x=1(&|$)/);
  await expect(
    page.getByRole("navigation", { name: "Settings" }).getByRole("link", {
      name: "Notification channels",
    }),
  ).toHaveAttribute("aria-current", "page");
  await expectCurrentHub(page, "Settings");
});

test("? opens the keyboard shortcuts and Esc closes them", async ({ page, workspace }) => {
  await page.goto(`${workspace.base}/overview`);
  await expect(rail(page)).toBeVisible();
  await page.keyboard.press("?");
  const help = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(help).toBeVisible();
  await expect(help).toContainText("Doctor");
  await expectNoAxeViolations(page);
  await page.keyboard.press("Escape");
  await expect(help).toBeHidden();
});

test("the icon rail at 1100 px and the stored collapse at 1440 px", async ({ page, workspace }) => {
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.goto(`${workspace.base}/overview`);
  await expect(rail(page).getByRole("link")).toHaveCount(8);
  await expectCurrentHub(page, "Overview");
  const railBox = await page.locator("aside").boundingBox();
  expect(railBox?.width, "the icon rail is 56 px wide").toBeLessThanOrEqual(56);
  await expect(page.getByRole("button", { name: "Collapse sidebar" })).toHaveCount(0);
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "Expand sidebar" }).click();
  const sheet = page.getByRole("dialog", { name: "Navigation" });
  await expect(sheet.getByRole("navigation", { name: "Main" }).getByRole("link")).toHaveCount(8);
  await sheet.getByRole("link", { name: "Monitor", exact: true }).click();
  await expect(sheet).toBeHidden();
  await expect(page).toHaveURL(atPath(workspace.base, "doctor"));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  await expectCurrentHub(page, "Monitor");
  await page.getByRole("button", { name: "Expand sidebar" }).click();
  await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeVisible();
});

test("the header shows controls a page can't honour disabled, with the reason", async ({
  page,
  workspace,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${workspace.base}/doctor?range=custom`);
  const range = page.getByRole("group", { name: "Time range" });
  await expect(range).toHaveAccessibleDescription(
    "Findings aren't filtered by time; each one has its own window.",
  );
  await expect(range.getByRole("radio", { name: /^24h/ })).toBeDisabled();
  await expect(page.getByRole("group", { name: "Environment" })).toHaveAccessibleDescription(
    "Findings cover every environment.",
  );
  // The shared range is never rewritten.
  await expect(page).toHaveURL(/range=custom/);

  await page.goto(`${workspace.base}/gateway?range=30d`);
  const presets = page.getByRole("radiogroup", { name: "Time range" });
  await expect(presets.getByRole("radio", { name: /^30d/ })).toBeDisabled();
  // The shared 30d reads as 7d here.
  await expect(presets.getByRole("radio", { name: /^7d/ })).toBeEnabled();
  await expect(presets.getByRole("radio", { name: /^7d/ })).toBeChecked();
  await expect(page.getByRole("button", { name: "Custom range" })).toBeDisabled();
  await expect(page).toHaveURL(/range=30d/);
});

test("at 375 px nothing scrolls sideways and the menu lists the 8 hubs", async ({
  page,
  workspace,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  for (const path of ["overview", "doctor", "gateway", "settings/project"]) {
    await page.goto(`${workspace.base}/${path}`);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await hasNoHorizontalScroll(page), `${path} scrolls sideways at 375 px`).toBe(true);
  }
  await page.getByRole("button", { name: "Open navigation" }).click();
  const sheet = page.getByRole("dialog", { name: "Navigation" });
  await expect(sheet.getByRole("navigation", { name: "Main" }).getByRole("link")).toHaveCount(8);

  // Below 768 px a hub's tabs sit behind the "Section" picker.
  await page.goto(`${workspace.base}/doctor`);
  await page.getByRole("button", { name: /^Section/ }).click();
  const picker = page.getByRole("dialog", { name: "Monitor" });
  await picker.getByRole("link", { name: "Alerts", exact: true }).click();
  await expect(page).toHaveURL(atPath(workspace.base, "alerts"));
});

test("axe (WCAG 2.2 AA) passes on the shell's pages at 1280 and 375 px", async ({
  page,
  workspace,
}) => {
  const heading = () => expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  await page.goto(`${workspace.base}/overview`);
  await checkAtWidths(page, "nav-overview", heading);

  await page.goto(`${workspace.base}/doctor`);
  await checkAtWidths(page, "nav-doctor", heading);

  await page.goto(`${workspace.base}/traces/${workspace.traceId}`);
  await checkAtWidths(page, "nav-trace", () =>
    expect(page.getByRole("heading", { level: 1, name: "nav-e2e" })).toBeVisible(),
  );
  // A detail page shows no hub tabs; Traces stays the current hub.
  await expect(page.getByRole("navigation", { name: "Traces sections" })).toHaveCount(0);
  await expectCurrentHub(page, "Traces");
  const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(crumbs).toBeVisible();
  await expect(crumbs.getByRole("link").first()).toHaveText("Traces");
  await expect(crumbs.getByRole("link", { name: "Traces" })).not.toHaveAttribute("aria-current");
  await expect(crumbs.locator("[aria-current]")).toHaveText("nav-e2e");

  await page.goto(`${workspace.base}/settings/channels`);
  await checkAtWidths(page, "nav-settings-channels", heading);
});
