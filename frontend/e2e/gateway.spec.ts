import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { createWorkspace, expect, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

// No provider is ever called: the credential holds a fake secret and nothing is sent through the
// gateway. The flow checks what an owner sets up before the first call.
const FAKE_PROVIDER_KEY = `sk-test-${"x".repeat(32)}`;
const GATEWAY_SECRET = /spl_gw_[A-Za-z0-9_]+/;

test("credential → route → key revealed once → Lab fault profile", async ({ page }) => {
  test.setTimeout(240_000);
  const { base } = await createWorkspace(page);

  // 1. A provider credential (a fake secret; nothing calls the provider).
  await page.goto(`${base}/gateway/credentials`);
  await expect(page.getByRole("heading", { name: "No provider credentials" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "Add credential" }).first().click();
  const credentialDialog = page.getByRole("dialog", { name: "Add credential" });
  await credentialDialog.getByLabel("Name").fill("anthropic-e2e");
  await credentialDialog.getByLabel("API key").fill(FAKE_PROVIDER_KEY);
  await expectNoAxeViolations(page);
  await credentialDialog.getByRole("button", { name: "Add credential" }).click();
  await expect(credentialDialog).toBeHidden();
  const credentials = page.getByRole("list", { name: "Provider credentials" });
  await expect(credentials).toContainText("anthropic-e2e");
  // The secret never comes back from the API.
  await expect(page.locator("body")).not.toContainText(FAKE_PROVIDER_KEY);

  // 2. A route on that credential.
  await page.goto(`${base}/gateway/routes`);
  await page.getByRole("button", { name: "Create route" }).first().click();
  const routeDialog = page.getByRole("dialog", { name: "New route" });
  await routeDialog.getByLabel("Name").fill("support-e2e");
  await routeDialog.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Creating support-e2e saves version 1.")).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "Create route" }).click();
  await expect(page).toHaveURL(/\/gateway\/routes\/[0-9a-f-]{36}$/);
  await expect(page.getByText('Created route "support-e2e".')).toBeVisible();

  // 3. A gateway key: the secret is shown once, then only its prefix.
  await page.goto(`${base}/gateway/keys`);
  await expect(page.getByRole("heading", { name: "No gateway keys" })).toBeVisible();
  await page.getByRole("button", { name: "Create key" }).first().click();
  const keyDialog = page.getByRole("dialog", { name: "Create gateway key" });
  await keyDialog.getByLabel("Name").fill("support-bot");
  await keyDialog.getByRole("combobox", { name: "Environment" }).click();
  await page.getByRole("option", { name: "staging" }).click();
  await expectNoAxeViolations(page);
  await keyDialog.getByRole("button", { name: "Create key" }).click();

  const reveal = page.getByRole("dialog", { name: "Key created" });
  await expect(
    reveal.getByText("Copy this key now. You won't be able to see it again."),
  ).toBeVisible();
  const secret = GATEWAY_SECRET.exec(await reveal.innerText())?.[0];
  expect(secret, "the gateway key secret is displayed").toBeTruthy();
  await expect(reveal).toContainText("Route support-e2e");
  await expectNoAxeViolations(page);
  await reveal.getByRole("button", { name: "Done" }).click();
  await expect(reveal).toBeHidden();
  await expect(page.getByText("support-bot", { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(secret ?? "never-shown");
  await page.reload();
  await expect(page.getByText("support-bot", { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(secret ?? "never-shown");

  // 4. A Lab fault profile attached to the staging key.
  await page.goto(`${base}/gateway/lab`);
  await expect(page.getByRole("heading", { name: "No fault profiles" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "New fault profile" }).first().click();
  const profileDialog = page.getByRole("dialog", { name: "New fault profile" });
  await profileDialog.getByLabel("Name").fill("rate-limit-drill");
  await profileDialog.getByRole("radio", { name: "Rate limited" }).click();
  await profileDialog.getByRole("checkbox", { name: /support-bot/ }).check();
  // The dialog's axe check is the separate test below (finding T9-01).
  await profileDialog.getByRole("button", { name: "Create profile" }).click();
  await expect(profileDialog).toBeHidden();
  await expect(page.getByRole("list", { name: "Fault profiles" })).toContainText(
    "rate-limit-drill",
  );
  await expectNoAxeViolations(page);
});

// Finding T9-01: the probability slider's thumb carries the slider role, so it carries the name.
test("the New fault profile dialog passes axe", async ({ page }) => {
  test.setTimeout(180_000);
  const { base } = await createWorkspace(page);
  await page.goto(`${base}/gateway/lab`);
  await page.getByRole("button", { name: "New fault profile" }).first().click();
  await expect(page.getByRole("dialog", { name: "New fault profile" })).toBeVisible();
  await expectNoAxeViolations(page);
});
