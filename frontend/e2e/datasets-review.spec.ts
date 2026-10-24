import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { createWorkspace, expect, sendTrace, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

const CSV = [
  "input,expected_output,topic",
  '"Where is my order?","It ships tomorrow.",shipping',
  '"Refund, please","{""status"": ""refunded""}",billing',
  ",missing input,broken",
  "",
].join("\r\n");

test("CSV upload: the preview counts what the server accepts", async ({ page }) => {
  test.setTimeout(180_000);
  const { base } = await createWorkspace(page);

  await page.goto(`${base}/datasets`);
  await page.getByRole("button", { name: "New dataset" }).first().click();
  const createDialog = page.getByRole("dialog", { name: "New dataset" });
  await createDialog.getByLabel("Name").fill("support-golden");
  await createDialog.getByRole("button", { name: /Create/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "support-golden" })).toBeVisible();
  await expect(page.getByText("This dataset has no items")).toBeVisible();
  await expectNoAxeViolations(page);

  await page.getByRole("button", { name: "Upload file" }).first().click();
  const upload = page.getByRole("dialog", { name: "Upload items" });
  await upload.locator('input[type="file"]').setInputFiles({
    name: "golden.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(`\uFEFF${CSV}`, "utf8"),
  });
  // Two good rows; the row without an input is flagged before anything is sent.
  await expect(upload.getByText("input is required")).toBeVisible();
  // The upload dialog's axe check is the separate test below (finding T9-02).
  await upload.getByRole("button", { name: "Upload 2 items" }).click();

  const finished = page.getByRole("dialog", { name: "Upload finished" });
  await expect(finished).toContainText(/Accepted\s*2/);
  await expect(finished).toContainText(/Rejected\s*1/);
  await expectNoAxeViolations(page);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("cell", { name: "Where is my order?" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Refund, please" })).toBeVisible();

  // "Run experiment" opens the new-experiment form with this dataset already picked.
  await page.getByRole("link", { name: "Run experiment" }).click();
  await expect(page).toHaveURL(/\/experiments\/new\?dataset=/);
  await expect(page.getByRole("combobox", { name: "Dataset" })).toHaveText(
    "support-golden · 2 items",
  );
  await expectNoAxeViolations(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoAxeViolations(page);
  await page.goBack();
  await expect(page.getByRole("heading", { level: 1, name: "support-golden" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.goto(`${base}/datasets`);
  // Phones get the tile list; the table is hidden below md.
  await expect(page.getByText("support-golden").filter({ visible: true })).toBeVisible();
  await expectNoAxeViolations(page);
});

// Finding T9-02: the upload dialog's file input is visually hidden but still focusable, so it
// needs its own accessible name.
test("the dataset upload dialog passes axe", async ({ page }) => {
  test.setTimeout(180_000);
  const { base } = await createWorkspace(page);
  await page.goto(`${base}/datasets`);
  await page.getByRole("button", { name: "Upload file" }).first().click();
  await expect(page.getByRole("dialog", { name: "Upload items" })).toBeVisible();
  await expectNoAxeViolations(page);
});

test("review queue: add a trace → claim → score with the keyboard → done", async ({ page }) => {
  test.setTimeout(240_000);
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });
  const traceId = await sendTrace(page, apiKey ?? "", { name: "needs-review" });

  // A yes/no score config to collect.
  await page.goto(`${base}/settings/project`);
  await page.getByRole("button", { name: "New score config" }).click();
  const configDialog = page.getByRole("dialog", { name: "New score config" });
  await configDialog.getByLabel("Name").fill("helpful");
  await configDialog.getByRole("button", { name: "boolean" }).click();
  await configDialog.getByRole("button", { name: "Create config" }).click();
  await expect(configDialog).toBeHidden();

  // The queue, with the trace in it.
  await page.goto(`${base}/queues`);
  await expect(page.getByRole("heading", { name: "No queues yet" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "New queue" }).first().click();
  const queueDialog = page.getByRole("dialog", { name: "New annotation queue" });
  await queueDialog.getByLabel("Name").fill("Weekly review");
  await queueDialog.getByLabel("Instructions").fill("Was the answer helpful?");
  await queueDialog.getByRole("checkbox", { name: /helpful/ }).check();
  await expectNoAxeViolations(page);
  await queueDialog.getByRole("button", { name: "Create queue" }).click();
  await expect(queueDialog).toBeHidden();
  // Creating the queue opens it.
  await expect(page.getByRole("heading", { level: 1, name: "Weekly review" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No items in this queue" })).toBeVisible();

  await page.getByRole("button", { name: "Add items" }).first().click();
  const addDialog = page.getByRole("dialog", { name: "Add items to Weekly review" });
  await addDialog.getByLabel("Trace ids").fill(traceId);
  await addDialog.getByRole("button", { name: "Add 1 item" }).click();
  await expect(addDialog).toBeHidden();
  await expect(page.getByRole("list", { name: "Queue items" }).getByRole("listitem")).toHaveCount(
    1,
  );
  await expectNoAxeViolations(page);

  // Work mode: the item is claimed once on arrival, even though the page remounts when the shell
  // switches to its focus layout (finding T9-03); "1" picks Yes, Enter submits.
  await page.getByRole("link", { name: "Start reviewing" }).click();
  await expect(page.getByLabel("Item to review")).toBeVisible();
  await expect(page.getByText("Hello from Playwright")).toBeVisible();
  await expectNoAxeViolations(page);
  await page.keyboard.press("1");
  await expect(page.getByRole("button", { name: /Yes/, pressed: true })).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "All done" })).toBeVisible();
  // After the submit, the "Last item" strip compares the score with the evaluator's (none here).
  const lastItem = page.getByRole("status").filter({ hasText: "Last item" });
  await expect(lastItem).toContainText("No evaluator score");
  await expect(lastItem.getByRole("link", { name: "Open trace" })).toHaveAttribute(
    "href",
    new RegExp(`/traces/${traceId}$`),
  );
  await expectNoAxeViolations(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoAxeViolations(page);
  await page.setViewportSize({ width: 1280, height: 720 });

  // The score is on the trace, in the inspector's Scores tab.
  await page.goto(`${base}/traces/${traceId}`);
  await page.getByRole("tab", { name: "Scores" }).click();
  await expect(page.getByText("helpful", { exact: true }).first()).toBeVisible();
});
