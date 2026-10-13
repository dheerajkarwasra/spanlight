import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { createWorkspace, expect, sendTrace, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

test("doctor, releases and users: honest empty states, then a release comparison", async ({
  page,
}) => {
  test.setTimeout(240_000);
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });

  // 1. Empty states on a fresh project.
  await page.goto(`${base}/doctor`);
  await expect(page.getByRole("heading", { name: /No findings yet|Nothing open/ })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.goto(`${base}/releases`);
  await expect(page.getByRole("heading", { name: "No releases in this window" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.goto(`${base}/users`);
  await expect(page.getByRole("heading", { name: "No end users in this window" })).toBeVisible();
  await expectNoAxeViolations(page);

  // 2. Spans tagged with a release and an end user.
  for (const release of ["v1", "v2"]) {
    await sendTrace(page, apiKey ?? "", { release, userId: "user-ada", name: `chat-${release}` });
  }
  await sendTrace(page, apiKey ?? "", { release: "v2", userId: "user-bob", status: "error" });

  // 3. The releases list is read live from traces: compare v1 with v2.
  await page.goto(`${base}/releases`);
  await expect(page.getByRole("heading", { name: "Compare releases" })).toBeVisible();
  await page.getByRole("combobox", { name: "Baseline (A)" }).click();
  await page.getByRole("option", { name: /^v1/ }).click();
  await page.getByRole("combobox", { name: "Candidate (B)" }).click();
  await page.getByRole("option", { name: /^v2/ }).click();
  await page.getByRole("button", { name: "Compare" }).click();
  const metrics = page.getByRole("table", { name: "Key metrics of both releases" });
  await expect(metrics).toBeVisible();
  await expect(metrics.getByRole("row", { name: /Traces/ })).toContainText("1");
  await expect(metrics.getByRole("row", { name: /Error rate/ })).toContainText("50.0%");
  await expectNoAxeViolations(page);

  // 4. The trace list shows the end user on the spans just sent.
  await page.goto(`${base}/traces`);
  await expect(page.getByRole("link", { name: "chat-v1" })).toBeVisible();
  await expectNoAxeViolations(page);
});
