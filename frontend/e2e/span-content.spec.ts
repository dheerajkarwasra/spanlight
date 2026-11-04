import { randomBytes } from "node:crypto";

import { e2eEnabled, expect, SKIP_REASON, test, uniqueEmail } from "./fixtures";
import { createWorkspace } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
test.use({ trace: "off", screenshot: "off", video: "off" });
test.afterEach(async ({ page }) => {
  await page.close();
});

/** Spans whose attributes alone pass the 8 MiB content budget: 150 × 60 KB, no input/output. */
const SPANS = 150;
const ATTRIBUTE_BYTES = 60_000;
const BATCH = 50;
const VISUAL_DIR = process.env.E2E_VISUAL_DIR;

test("an attributes-heavy trace loads bounded; the selected span fetches its content once", async ({
  page,
}) => {
  test.setTimeout(150_000);
  await page.goto("/signup");
  await page.getByLabel("Name").fill("S9 User");
  await page.getByLabel("Work email").fill(uniqueEmail());
  await page.getByLabel("Password").fill("correct-horse-battery");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/onboarding/);
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });

  const traceId = randomBytes(16).toString("hex");
  const start = Date.now() - 60_000;
  const spanIds = Array.from({ length: SPANS }, () => randomBytes(8).toString("hex"));
  for (let offset = 0; offset < SPANS; offset += BATCH) {
    const response = await page.request.post("/v1/traces", {
      headers: { Authorization: `Bearer ${apiKey ?? ""}` },
      data: {
        spans: spanIds.slice(offset, offset + BATCH).map((spanId, i) => ({
          trace_id: traceId,
          span_id: spanId,
          parent_span_id: offset + i === 0 ? null : spanIds[0],
          name: `attrs-${offset + i}`,
          kind: "llm",
          status: "ok",
          start_time: new Date(start + (offset + i) * 10).toISOString(),
          end_time: new Date(start + (offset + i) * 10 + 5).toISOString(),
          provider: "openai",
          model: "gpt-4o-mini",
          usage: { input_tokens: 12, output_tokens: 40 },
          attributes: { "app.index": offset + i, "app.context": "x".repeat(ATTRIBUTE_BYTES) },
          trace: { name: "S9 heavy", environment: "e2e" },
        })),
      },
    });
    expect(response.ok(), await response.text()).toBe(true);
  }

  // Completed single-span reads (not `/spans/{id}/replay-source` and the like). Responses, not
  // requests: the dev build runs under StrictMode, whose double mount aborts the first request.
  const spanReads: string[] = [];
  let projectId = "";
  page.on("response", (response) => {
    const detail = /\/api\/v1\/projects\/([^/]+)\/traces\/[^/?]+(\/spans\/[0-9a-f]+)?(\?|$)/.exec(
      response.url(),
    );
    if (detail?.[1]) projectId = detail[1];
    if (detail?.[2] && response.ok()) spanReads.push(response.url());
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${base}/traces/${traceId}`);
  await expect(page.getByRole("heading", { level: 2, name: /150 spans/ })).toBeVisible();
  // Attribute-only omission is not a partial search.
  await expect(page.getByText("Search covers span names and loaded input/output.")).toHaveCount(0);
  // The root (span 0) fits; nothing to fetch for it.
  expect(spanReads).toHaveLength(0);

  // The public contract (the page's own read was bounded): unqualified reads are refused,
  // bounded reads return every span.
  const detailPath = `/api/v1/projects/${projectId}/traces/${traceId}`;
  const refused = await page.request.get(detailPath);
  expect(refused.status()).toBe(422);
  expect(((await refused.json()) as { code: string }).code).toBe("SPAN_CONTENT_TOO_LARGE");
  const bounded = await page.request.get(`${detailPath}?span_content=bounded`);
  expect(bounded.status()).toBe(200);
  const detail = (await bounded.json()) as {
    spans: { attributes_omitted: boolean; payload_omitted: boolean; attributes: object }[];
  };
  expect(detail.spans).toHaveLength(SPANS);
  expect(detail.spans.at(-1)?.attributes_omitted).toBe(true);
  expect(detail.spans.at(-1)?.payload_omitted).toBe(false);
  expect(detail.spans.at(-1)?.attributes).toEqual({});

  // A trace over 100 spans starts with its healthy subtrees folded.
  await page.getByRole("button", { name: "Expand all" }).click();
  const last = `attrs-${SPANS - 1}`;
  await page.getByRole("treeitem", { name: new RegExp(last) }).click();
  await page.getByRole("tab", { name: "Attributes" }).click();
  await expect(page.getByRole("heading", { level: 2, name: last })).toBeVisible();
  const loading = page.getByRole("status").filter({ hasText: "Loading span content…" });
  if (VISUAL_DIR && (await loading.count()) > 0) {
    await page.screenshot({ path: `${VISUAL_DIR}/desktop-attributes-loading.png` });
  }
  await expect(page.getByText(`"app.index"`)).toBeVisible();
  await expect(page.getByText("No custom attributes on this span.")).toHaveCount(0);
  expect(spanReads).toHaveLength(1);
  expect(spanReads[0]).toContain(`/spans/${spanIds[SPANS - 1]}`);

  await page.getByRole("tab", { name: "Raw JSON" }).click();
  await expect(page.getByRole("button", { name: "Copy JSON" })).toBeEnabled();
  await expect(page.getByText('"attributes_omitted"')).toBeVisible();
  expect(spanReads).toHaveLength(1);
  if (VISUAL_DIR) {
    await page.screenshot({ path: `${VISUAL_DIR}/desktop-raw-ready.png` });
  }

  // Failure and retry: the next selected span's read fails until the route is released.
  const previous = `attrs-${SPANS - 2}`;
  const previousRead = `**/spans/${spanIds[SPANS - 2] ?? ""}`;
  await page.route(previousRead, (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/problem+json",
      body: JSON.stringify({ code: "SERVICE_UNAVAILABLE", detail: "injected" }),
    }),
  );
  await page.getByRole("treeitem", { name: new RegExp(previous) }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Couldn’t load span content" }),
  ).toBeVisible();
  await expect(page.getByRole("tab", { name: "Raw JSON" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("button", { name: "Copy JSON" })).toBeDisabled();
  await page.setViewportSize({ width: 375, height: 1100 });
  if (VISUAL_DIR) {
    await page.getByRole("button", { name: "Retry" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${VISUAL_DIR}/phone-raw-failure.png` });
  }
  await page.unroute(previousRead);
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("button", { name: "Copy JSON" })).toBeEnabled();
  expect(spanReads).toHaveLength(2);
  expect(spanReads[1]).toContain(`/spans/${spanIds[SPANS - 2]}`);
});
