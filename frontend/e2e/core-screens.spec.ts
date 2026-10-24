import { randomBytes } from "node:crypto";

import type { Page } from "@playwright/test";

import { dbEnabled, DB_SKIP_REASON, refreshUserStats } from "./db-seed";
import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import {
  checkAtWidths,
  createWorkspace,
  expect,
  sendTrace,
  test as base,
  type SpanInput,
} from "./workspace";

/*
 * The core screens (UX Phase 3): the traces table's sort, column settings and peek; the trace
 * page's tab in the URL, Copy link and Compare with the previous run; the compare diff; Sessions
 * and Users search and sort; an Overview chart bucket opening its traces; and axe with the
 * WCAG 2.2 tags on each page at 1280 and 375 px.
 */

const LONG_NAME = "core-summarise-the-customer-conversation-and-draft-a-reply";

interface CoreWorkspace {
  base: string;
  /** The later of the two `core-run` traces; the earlier one is its previous run. */
  runId: string;
  longId: string;
  /** A trace whose tree has a failed retry group beside chain, retrieval and tool spans. */
  retryId: string;
}

/**
 * Sends a support-ticket trace through `POST /v1/traces`: a chain root over a retrieval, a tool,
 * three attempts of one model call (same model and input, so one request hash; the first two
 * failed), another model call and a second tool. Eight spans, two failed, six tree rows.
 */
async function sendRetryTrace(page: Page, apiKey: string): Promise<string> {
  const traceId = randomBytes(16).toString("hex");
  const rootId = randomBytes(8).toString("hex");
  const start = Date.now() - 120_000;
  const llm = (content: string) => ({
    provider: "openai",
    model: "gpt-4o-mini",
    usage: { input_tokens: 900, output_tokens: 120 },
    input: [{ role: "user", content }],
    output: { role: "assistant", content: "Here is the reply." },
  });
  const children: [string, string, number, number, object][] = [
    ["retrieve_knowledge_base_articles", "retrieval", 100, 800, {}],
    ["lookup_customer_order", "tool", 1000, 600, {}],
    ["draft_reply_with_model", "llm", 1700, 400, { ...llm("Draft"), status: "error" }],
    ["draft_reply_with_model", "llm", 2200, 400, { ...llm("Draft"), status: "error" }],
    ["draft_reply_with_model", "llm", 2700, 1000, llm("Draft")],
    ["final_answer_completion", "llm", 3800, 800, llm("Answer")],
    ["post_process_output", "tool", 4700, 250, {}],
  ];
  const span = (name: string, kind: string, offset: number, ms: number, extra: object) => ({
    trace_id: traceId,
    span_id: randomBytes(8).toString("hex"),
    parent_span_id: rootId,
    name,
    kind,
    status: "ok",
    start_time: new Date(start + offset).toISOString(),
    end_time: new Date(start + offset + ms).toISOString(),
    ...extra,
  });
  const spans = [
    {
      // Shorter than core-slow (6 s), which the duration sort expects first.
      ...span("core-retry", "chain", 0, 5000, {}),
      span_id: rootId,
      parent_span_id: null,
      trace: { name: "core-retry", environment: "e2e" },
    },
    ...children.map((child) => span(...child)),
  ];
  const response = await page.request.post("/v1/traces", {
    headers: { Authorization: `Bearer ${apiKey}` },
    data: { spans },
  });
  expect(response.ok(), await response.text()).toBe(true);
  return traceId;
}

/** One workspace for the whole spec, its traces ending one to three minutes ago. */
const test = base.extend<object, { workspace: CoreWorkspace }>({
  workspace: [
    async ({ browser, accountState }, provide, workerInfo) => {
      const context = await browser.newContext({
        baseURL: workerInfo.project.use.baseURL,
        storageState: accountState,
      });
      const page = await context.newPage();
      const { base, apiKey } = await createWorkspace(page, { apiKey: true });
      const ago = (seconds: number) => new Date(Date.now() - seconds * 1000);
      const send = (span: SpanInput) => sendTrace(page, apiKey ?? "", span);
      await send({
        name: "core-slow",
        durationMs: 6000,
        endTime: ago(180),
        sessionId: "sess-alpha",
        userId: "user-alpha",
        usage: { input_tokens: 4000, output_tokens: 1500 },
      });
      await send({
        name: "core-fast",
        durationMs: 300,
        endTime: ago(170),
        sessionId: "sess-alpha",
        userId: "user-alpha",
      });
      await send({
        name: "core-mid",
        durationMs: 1500,
        endTime: ago(160),
        sessionId: "sess-beta",
        userId: "user-beta",
      });
      await send({ name: "core-run", endTime: ago(150), output: "The refund was approved." });
      const runId = await send({
        name: "core-run",
        endTime: ago(140),
        output: "The refund was declined.",
      });
      const longId = await send({ name: LONG_NAME, endTime: ago(130) });
      const retryId = await sendRetryTrace(page, apiKey ?? "");
      await context.close();
      await provide({ base, runId, longId, retryId });
    },
    { scope: "worker", timeout: 120_000 },
  ],
});

test.skip(!e2eEnabled, SKIP_REASON);
test.describe.configure({ timeout: 240_000 });

function tracesTable(page: Page) {
  return page.getByRole("table", { name: "Traces" });
}

test("traces sort by duration, and a hidden column stays hidden after a reload", async ({
  page,
  workspace,
}) => {
  // The Model column shows from a 1100 px wide table, beside the Input and Output previews.
  await page.setViewportSize({ width: 1920, height: 1000 });
  await page.goto(`${workspace.base}/traces`);
  const table = tracesTable(page);
  await expect(table.getByRole("link", { name: "core-slow" })).toBeVisible();

  await table.getByRole("button", { name: "Sort by Duration" }).click();
  await expect(page).toHaveURL(/[?&]sort=duration/);
  await expect(table.getByRole("columnheader", { name: "Duration" })).toHaveAttribute(
    "aria-sort",
    "descending",
  );
  await expect(table.locator("a[data-trace-id]").first()).toHaveText("core-slow");

  await expect(table.getByRole("columnheader", { name: "Model" })).toBeVisible();
  await page.getByRole("button", { name: "Table settings" }).click();
  await page.getByRole("menuitemcheckbox", { name: "Model" }).click();
  await page.keyboard.press("Escape");
  await expect(table.getByRole("columnheader", { name: "Model" })).toHaveCount(0);
  await page.reload();
  await expect(tracesTable(page).getByRole("link", { name: "core-slow" })).toBeVisible();
  await expect(tracesTable(page).getByRole("columnheader", { name: "Model" })).toHaveCount(0);
});

test("the peek opens at 1440, j moves on, Back closes, Open full page keeps the span", async ({
  page,
  workspace,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${workspace.base}/traces`);
  const table = tracesTable(page);
  const rows = table.locator("a[data-trace-id]");
  await expect(rows.first()).toBeVisible();
  const firstName = await rows.nth(0).innerText();
  const secondName = await rows.nth(1).innerText();

  await rows.nth(0).click();
  const sheet = page.getByRole("dialog", { name: firstName });
  await expect(sheet).toBeVisible();
  await expect(page).toHaveURL(/[?&]peek=/);
  // Focus moves to the sheet's title (the first of its headings; the trace view has its own).
  await expect(sheet.getByRole("heading", { name: firstName }).first()).toBeFocused();

  await page.keyboard.press("j");
  await expect(page.getByRole("dialog", { name: secondName })).toBeVisible();

  await page.goBack();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page).not.toHaveURL(/[?&]peek=/);

  // axe with the peek open, once (the peek shows from 1280 px only).
  await page.setViewportSize({ width: 1280, height: 900 });
  await rows.nth(0).click();
  const peek = page.getByRole("dialog", { name: firstName });
  await expect(peek.getByRole("treeitem").first()).toBeVisible();
  await expectNoAxeViolations(page);

  const span = peek.getByRole("treeitem").first();
  const spanId = ((await span.getAttribute("id")) ?? "").replace("span-row-", "");
  expect(spanId).toMatch(/^[0-9a-f]{16}$/);
  await span.click();
  await peek.getByRole("tab", { name: "Attributes" }).click();
  await peek.getByRole("link", { name: "Open full page" }).click();
  await expect(page).toHaveURL(/\/traces\/[0-9a-f]{32}\?/);
  const opened = new URL(page.url()).searchParams;
  expect(opened.get("span")).toBe(spanId);
  expect(opened.get("tab")).toBe("attributes");
  await expect(page.getByRole("tab", { name: "Attributes", selected: true })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: firstName })).toBeVisible();
});

test("a trace page opens the tab in its URL, copies its link and compares with the previous run", async ({
  page,
  context,
  workspace,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto(`${workspace.base}/traces/${workspace.runId}?tab=cost`);
  await expect(page.getByRole("tab", { name: "Cost", selected: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cost breakdown" })).toBeVisible();

  await page.getByRole("button", { name: "Copy link" }).click();
  await expect(page.getByText("Link copied")).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(new URL(copied).searchParams.get("tab")).toBe("cost");
  expect(new URL(copied).pathname).toBe(`${workspace.base}/traces/${workspace.runId}`);

  await page.getByRole("button", { name: "Compare with…" }).click();
  const previous = page.getByRole("region", { name: "Previous run of core-run" });
  await previous.getByRole("link").click();
  await expect(page).toHaveURL(
    new RegExp(`/traces/compare\\?.*a=[0-9a-f]{32}.*b=${workspace.runId}`),
  );

  await page.getByRole("button", { name: "Show diff of core-run" }).click();
  const after = page.getByRole("list", { name: "B · Candidate output" });
  await expect(after).toContainText("declined");
  await expect(page.getByRole("list", { name: "A · Baseline output" })).toContainText("approved");
  await checkAtWidths(page, "core-compare", () =>
    expect(page.getByRole("heading", { level: 1 })).toBeVisible(),
  );
});

test("a trace with a failed retry group keeps its tree toolbar on one row, first row high up", async ({
  page,
  workspace,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${workspace.base}/traces/${workspace.retryId}`);
  const toolbar = page.locator('[data-slot="tree-toolbar"]');
  await expect(toolbar.getByRole("heading", { name: "8 spans · 2 failed" })).toBeVisible();
  await expect(toolbar.getByRole("switch", { name: "Collapse retries" })).toBeVisible();
  await expect(page.getByText("3 attempts · 2 failed")).toBeVisible();
  // Exactly one kind control is exposed at a time: pills in a wide pane, the select otherwise.
  const kindControls = async () =>
    (await page.getByRole("radiogroup", { name: "Span kind", exact: true }).count()) +
    (await page.getByRole("combobox", { name: "Kind", exact: true }).count());
  expect(await kindControls()).toBe(1);
  // One 36 px row (spec §6 D7).
  await expect.poll(async () => (await toolbar.boundingBox())?.height ?? 0).toBeLessThanOrEqual(40);
  // Spec §1: ≤ 160 px from the top of the breadcrumbs to the first span row at 1440×900. Polled:
  // the Doctor badges in the summary bar arrive after the tree.
  const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
  const firstRow = page.getByRole("treeitem").first();
  await expect
    .poll(async () => {
      const [top, row] = [await crumbs.boundingBox(), await firstRow.boundingBox()];
      return top && row ? row.y - top.y : Infinity;
    })
    .toBeLessThanOrEqual(160);

  // Spec §6: stacked, the 220 px cap holds the rows only, so at least four rows show under it.
  await page.setViewportSize({ width: 375, height: 812 });
  const rows = page.locator('[data-slot="tree-rows"]');
  await expect(rows.getByRole("searchbox", { name: "Find span" })).toHaveCount(0);
  await expect
    .poll(() =>
      rows.evaluate((box) => {
        const frame = box.getBoundingClientRect();
        return [...box.querySelectorAll('[role="treeitem"]')].filter((row) => {
          const rect = row.getBoundingClientRect();
          return rect.top >= frame.top - 1 && rect.bottom <= frame.bottom + 1;
        }).length;
      }),
    )
    .toBeGreaterThanOrEqual(4);
  expect(await kindControls()).toBe(1);
  // axe on the switch's icon label and the Kind select, at 1280 and 375.
  await checkAtWidths(page, "core-trace-retry", () => expect(toolbar).toBeVisible());
});

test("the trace title keeps its width beside the summary bar's actions", async ({
  page,
  workspace,
}) => {
  await page.goto(`${workspace.base}/traces/${workspace.longId}`);
  const title = page.getByRole("heading", { level: 1, name: LONG_NAME });
  for (const width of [1024, 1280, 1440, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(title).toBeVisible();
    // At least 10rem less the status icon; the bug left it 0 px wide at 1280.
    await expect.poll(async () => (await title.boundingBox())?.width ?? 0).toBeGreaterThan(130);
  }
  await checkAtWidths(page, "core-trace", () => expect(title).toBeVisible());
});

test("sessions search and sort", async ({ page, workspace }) => {
  await page.goto(`${workspace.base}/sessions`);
  await expect(page.getByRole("link", { name: "sess-alpha" }).first()).toBeVisible();
  // The default sort stays out of the URL, as on the traces list.
  await expect(page).not.toHaveURL(/[?&](sort|order)=/);
  await expect(page.getByRole("link", { name: "sess-beta" }).first()).toBeVisible();

  await page.getByRole("button", { name: "Sort by Cost" }).click();
  await expect(page).toHaveURL(/[?&]sort=cost/);
  await expect(page.getByRole("columnheader", { name: "Cost" })).toHaveAttribute(
    "aria-sort",
    "descending",
  );
  // sess-alpha holds the 4 000 + 1 500 token call, so it costs the most.
  const sessions = page
    .getByRole("table", { name: "Sessions" })
    .getByRole("link", { name: /^sess-/ });
  await expect(sessions).toHaveText(["sess-alpha", "sess-beta"]);

  await page.getByRole("searchbox", { name: "Search by session ID or user ID" }).fill("sess-beta");
  await expect(page).toHaveURL(/[?&]q=sess-beta/);
  await expect(page.getByRole("link", { name: "sess-alpha" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "sess-beta" }).first()).toBeVisible();
  await checkAtWidths(page, "core-sessions", async () => {
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    // A phone asks without previews, so the list refetches dimmed (aria-busy); check it settled.
    // The refetch starts on the render after the resize, so wait for the network to go quiet first.
    await page.waitForLoadState("networkidle");
    await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
    // ...and has faded back in (the dimming is an opacity transition).
    await page.waitForFunction(() =>
      [...document.querySelectorAll("[aria-label='Sessions']")].every(
        (list) => getComputedStyle(list).opacity === "1",
      ),
    );
  });
});

test("users search", async ({ page, workspace }) => {
  test.skip(!dbEnabled, DB_SKIP_REASON);
  // The Users list reads the per-user stats the worker refreshes every 15 minutes: run a pass now.
  await refreshUserStats();
  await page.goto(`${workspace.base}/users`);
  await expect(page.getByRole("link", { name: "user-alpha" }).first()).toBeVisible();

  await page.getByRole("searchbox", { name: "Search by user ID" }).fill("user-beta");
  await expect(page).toHaveURL(/[?&]q=user-beta/);
  await expect(page.getByRole("link", { name: "user-alpha" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "user-beta" }).first()).toBeVisible();
  await checkAtWidths(page, "core-users", () =>
    expect(page.getByRole("heading", { level: 1 })).toBeVisible(),
  );
});

test("an Overview chart bucket opens its traces", async ({ page, workspace }) => {
  await page.goto(`${workspace.base}/overview`);
  const chart = page.getByRole("img", { name: /^LLM calls and failed calls per/ });
  // The tallest bar holds this spec's traces.
  const tallestBar = () =>
    chart.evaluate((node) => {
      const bars = [...node.querySelectorAll(".recharts-bar-rectangle")].map((bar) =>
        bar.getBoundingClientRect(),
      );
      const tallest = bars.reduce((a, b) => (b.height > a.height ? b : a), new DOMRect());
      return { x: tallest.x + tallest.width / 2, y: tallest.bottom - 2, height: tallest.height };
    });
  await expect.poll(async () => (await tallestBar()).height).toBeGreaterThan(0);
  await chart.scrollIntoViewIfNeeded();
  const box = await tallestBar();
  // The chart reads the bucket under the pointer: hover it (its tooltip shows), then click.
  await page.mouse.move(box.x, box.y);
  await expect(page.locator(".recharts-tooltip-wrapper")).toBeVisible();
  await page.mouse.click(box.x, box.y);
  await expect(page).toHaveURL(/\/traces\?.*range=custom/);
  await expect(page).toHaveURL(/[?&]from=.*[?&]to=/);
  await expect(tracesTable(page).getByRole("link", { name: "core-slow" })).toBeVisible();
});

test("axe (WCAG 2.2 AA) passes on Overview and Traces at 1280 and 375 px", async ({
  page,
  workspace,
}) => {
  const heading = () => expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.goto(`${workspace.base}/overview`);
  await checkAtWidths(page, "core-overview", heading);
  await page.goto(`${workspace.base}/traces`);
  await checkAtWidths(page, "core-traces", heading);
});
