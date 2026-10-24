import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { createWorkspace, expect, sendTrace, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

test("heuristic evaluator: test run, then scores on sampled traces", async ({ page }) => {
  // The worker samples new traces once a minute, so this waits up to about three minutes.
  test.setTimeout(300_000);
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });
  await sendTrace(page, apiKey ?? "", {
    name: "greeting",
    output: { role: "assistant", content: "Hello back" },
  });

  await page.goto(`${base}/evaluators`);
  await expect(page.getByRole("heading", { name: "No evaluators yet" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("link", { name: "New evaluator" }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: "New evaluator" })).toBeVisible();
  await page.getByRole("textbox", { name: "Name", exact: true }).fill("says-hello");
  await page.getByLabel("Pattern").fill("Hello");
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "Create evaluator" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "says-hello" })).toBeVisible();
  await expectNoAxeViolations(page);

  // A test run scores one trace on demand and stores nothing.
  await page.getByRole("button", { name: "Test run" }).click();
  const testDialog = page.getByRole("dialog", { name: "Test run" });
  await testDialog.getByRole("radio", { name: /greeting/ }).check();
  await testDialog.getByRole("button", { name: "Run test" }).click();
  await expect(testDialog.getByRole("button", { name: "Run again" })).toBeVisible();
  await expectNoAxeViolations(page);
  await testDialog.getByRole("button", { name: "Close" }).first().click();

  // A trace sent after the evaluator exists is sampled (100 %) and scored by the worker.
  await sendTrace(page, apiKey ?? "", {
    name: "greeting-2",
    output: { role: "assistant", content: "Hello again" },
  });
  const runs = page.getByRole("list", { name: "Runs" });
  await expect(async () => {
    await page.reload();
    await expect(runs.getByRole("listitem").first()).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 200_000, intervals: [10_000] });
  await expectNoAxeViolations(page);
});

test("experiment: without a provider credential the form says so instead of running", async ({
  page,
}) => {
  test.setTimeout(240_000);
  const { base } = await createWorkspace(page);

  await page.goto(`${base}/experiments`);
  await expect(page.getByRole("heading", { name: "No experiments yet" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.goto(`${base}/experiments/new`);
  await expect(page.getByRole("heading", { level: 1, name: "New experiment" })).toBeVisible();
  await expect(
    page.getByText("No credentials yet. Add one under Gateway, Credentials."),
  ).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("textbox", { name: "Name", exact: true }).fill("baseline");
  await page.getByRole("button", { name: "Create experiment" }).click();
  await expect(page.getByText("Choose a credential.")).toBeVisible();
  await expect(page).toHaveURL(/\/experiments\/new$/);

  // The same pages at the 375 px minimum width.
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoAxeViolations(page);
  await page.goto(`${base}/experiments`);
  await expect(page.getByRole("heading", { name: "No experiments yet" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.goto(`${base}/evaluators`);
  await expect(page.getByRole("heading", { name: "No evaluators yet" })).toBeVisible();
  await expectNoAxeViolations(page);
});

// The compare page's "Regressions only" switch and "Show diff" need two finished runs, and a run
// makes real provider calls, so this suite does not reach them (no fake rows on the product path).
