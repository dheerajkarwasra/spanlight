import {
  e2eEnabled,
  expect,
  expectNoAxeViolations,
  SKIP_REASON,
  test,
  uniqueEmail,
} from "./fixtures";

test.skip(!e2eEnabled, SKIP_REASON);

const SECRET_PATTERN = /spl_live_[A-Za-z0-9]+_[A-Za-z0-9]+/;

test("signup → onboarding → first trace → trace detail", async ({ page }) => {
  test.setTimeout(90_000);

  // 1. Sign up.
  await page.goto("/signup");
  await page.getByLabel("Name").fill("E2E User");
  await page.getByLabel("Work email").fill(uniqueEmail());
  await page.getByLabel("Password").fill("correct-horse-battery");
  await page.getByRole("button", { name: "Create account" }).click();

  // 2. Organization and project.
  await expect(page).toHaveURL(/\/onboarding/);
  await page.getByLabel("Organization name").fill("E2E Org");
  // "Continue" creates the organization (its accessible description says so).
  const createOrg = page.getByRole("button", { name: "Continue" });
  await expect(createOrg).toHaveAccessibleDescription("Creates your organization");
  await createOrg.click();
  await expect(page.getByRole("heading", { level: 1, name: "E2E Org is ready." })).toBeVisible();
  await page.getByLabel("Project name").fill("E2E Project");
  await page.getByRole("button", { name: "Create project" }).click();

  // 3. API key, shown once.
  await page.getByRole("button", { name: "Create API key" }).click();
  // The hint below the key form says the same, so wait on the reveal-only Copy button first.
  await expect(page.getByRole("button", { name: "Copy API key" })).toBeVisible();
  await expect(page.getByText("Shown once. Revoke it in Settings › API keys.")).toBeVisible();
  const pageText = await page.locator("main").innerText();
  const secret = SECRET_PATTERN.exec(pageText)?.[0];
  expect(secret, "the new API key secret is displayed").toBeTruthy();
  await expect(page.getByText("Waiting for your first trace…")).toBeVisible();
  await expectNoAxeViolations(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoAxeViolations(page);

  // 4. "Send a test trace" posts one real span with the key on screen.
  await page.getByRole("button", { name: "Send a test trace" }).click();
  await expect(page.getByText("Test trace sent. Waiting for it to arrive…")).toBeVisible();

  // 5. The waiting state flips to success (polls every 2 s).
  await expect(page.getByText("First trace received")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { level: 1, name: "You’re connected." })).toBeVisible();
  await expect(page.getByRole("list", { name: "First trace" })).toContainText(
    "Test trace from setup",
  );
  await expectNoAxeViolations(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  await expectNoAxeViolations(page);
  // The key secret is still on screen, so the page offers the trace instead of leaving.
  await expect(page.getByRole("button", { name: "Copy API key" })).toBeVisible();
  await page.getByRole("link", { name: "Open your first trace" }).click();

  // 6. Trace detail renders the test span's payload.
  await expect(page).toHaveURL(/\/traces\/[0-9a-f]{32}/);
  await expect(page.getByText("Sent from the setup page").first()).toBeVisible();
  await expectNoAxeViolations(page);
});
