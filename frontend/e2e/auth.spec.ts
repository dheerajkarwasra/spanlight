import { e2eEnabled, expect, expectNoAxeViolations, SKIP_REASON, test } from "./fixtures";

test.skip(!e2eEnabled, SKIP_REASON);

test.describe("public pages", () => {
  test("login page is accessible and offers the live demo", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Welcome back." })).toBeVisible();
    await expect(page.getByRole("button", { name: "Try the live demo" })).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("signup validates the password length before submitting", async ({ page }) => {
    await page.goto("/signup");
    await page.getByLabel("Name").fill("E2E User");
    await page.getByLabel("Work email").fill("someone@example.com");
    await page.getByLabel("Password").fill("short");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByText("Use at least 10 characters.")).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("a pasted password stays in the field", async ({ page }) => {
    await page.goto("/login");
    // Copy from the email field, then paste into the password field (password managers paste too).
    const email = page.getByLabel("Email");
    await email.fill("pasted-secret-123");
    await email.selectText();
    await page.keyboard.press("ControlOrMeta+C");
    const password = page.getByLabel("Password", { exact: true });
    await password.focus();
    await page.keyboard.press("ControlOrMeta+V");
    await expect(password).toHaveValue("pasted-secret-123");
    await page.setViewportSize({ width: 375, height: 812 });
    await expectNoAxeViolations(page);
  });

  test("unauthenticated visitors are sent to login", async ({ page }) => {
    // "/" is the public landing page (see landing.spec.ts); app pages still require a session.
    await page.goto("/onboarding");
    await expect(page).toHaveURL(/\/login/);
  });
});
