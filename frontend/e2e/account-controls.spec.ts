import type { Page } from "@playwright/test";

import { clearTermsAcceptance, dbEnabled, DB_SKIP_REASON } from "./db-seed";
import {
  e2eEnabled,
  expect,
  expectNoAxeViolations,
  SKIP_REASON,
  test,
  uniqueEmail,
} from "./fixtures";
import { createWorkspace } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The terms line and gate need both halves: an api with TERMS_VERSION set and a web build with the
// legal pages (VITE_LEGAL_PAGES=1). The API allows 10 sign-ups per IP address per hour: most tests
// sign up once, the member test twice (an owner and the member), the confirm-page test not at all.
test.skip(
  process.env.E2E_LEGAL_PAGES !== "1" || process.env.E2E_TERMS !== "1",
  "Set E2E_LEGAL_PAGES=1 and E2E_TERMS=1 against a web build with VITE_LEGAL_PAGES=1 and an api with TERMS_VERSION set.",
);

const PASSWORD = "correct-horse-battery";

async function signUp(page: Page, email: string): Promise<void> {
  await page.goto("/signup");
  // The form sends `accept_terms` only once the line is on screen.
  await expect(page.getByText(/^By creating an account you agree to the/).last()).toBeVisible();
  await page.getByLabel("Name").fill("E2E Terms");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
}

/** Signs up and creates an organization and project; returns Settings › Account's path. */
async function signUpWithWorkspace(page: Page, email: string = uniqueEmail()): Promise<string> {
  await signUp(page, email);
  await expect(page).toHaveURL(/\/onboarding/, { timeout: 15_000 });
  const { base } = await createWorkspace(page);
  return `${base}/settings/account`;
}

/** A one-use link that adds whoever opens it to the caller's first organization as a member. */
async function createMemberInvite(page: Page): Promise<string> {
  const me = await page.request.get("/api/v1/auth/me");
  const { memberships } = (await me.json()) as { memberships: { org: { id: string } }[] };
  const orgId = memberships[0]?.org.id ?? "";
  const url = await page.evaluate(
    async ({ id, password }) => {
      const csrf = document.cookie
        .split("; ")
        .find((cookie) => cookie.startsWith("spl_csrf="))
        ?.slice("spl_csrf=".length);
      const response = await fetch(`/api/v1/orgs/${id}/invites`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": decodeURIComponent(csrf ?? ""),
        },
        body: JSON.stringify({ role: "member", password }),
      });
      return ((await response.json()) as { url: string }).url;
    },
    { id: orgId, password: PASSWORD },
  );
  const { pathname, hash } = new URL(url);
  return `${pathname}${hash}`;
}

test("signup shows the terms line and lands without a gate", async ({ page }) => {
  await page.goto("/signup");
  // Under Create account; a server with GitHub or Google shows the same line under those too.
  const line = page.getByText(/^By creating an account you agree to the/).last();
  await expect(line).toBeVisible();
  await expect(line.getByRole("link")).toHaveCount(3);
  await expect(line.getByRole("link", { name: "Terms" })).toHaveAttribute("href", "/terms");
  await expect(line.getByRole("link", { name: "Privacy policy" })).toHaveAttribute(
    "href",
    "/privacy",
  );
  await expect(line.getByRole("link", { name: "DPA" })).toHaveAttribute("href", "/dpa");

  await signUp(page, uniqueEmail());
  await expect(page).toHaveURL(/\/onboarding/);
  expect(new URL(page.url()).pathname).not.toBe("/accept-terms");
});

test("a user without acceptance is gated once", async ({ page }) => {
  test.skip(!dbEnabled, DB_SKIP_REASON);
  const email = uniqueEmail();
  await signUp(page, email);
  await expect(page).toHaveURL(/\/onboarding/);
  clearTermsAcceptance(email);

  // A full load reads `me` afresh, as signing in does.
  await page.goto("/onboarding");
  await expect(page).toHaveURL(/\/accept-terms\?next=/);
  await expect(page.getByRole("heading", { level: 1, name: "Accept the terms" })).toBeVisible();
  await expect(page.getByText(/^Terms version .+ · Signed in as /)).toContainText(email);
  await expectNoAxeViolations(page);

  await page.evaluate(() => {
    window.localStorage.setItem("spanlight-theme", "dark");
  });
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await expect(page.getByRole("heading", { level: 1, name: "Accept the terms" })).toBeVisible();
  await expectNoAxeViolations(page);

  await page.getByRole("button", { name: "Accept and continue" }).click();
  await expect(page).toHaveURL(/\/onboarding/);
  await page.reload();
  await expect(page).toHaveURL(/\/onboarding/);
  await page.goto("/accept-terms");
  await expect(page).not.toHaveURL(/\/accept-terms/);
});

test("name can be edited", async ({ page }) => {
  const account = await signUpWithWorkspace(page);
  await page.goto(account);
  const name = page.getByLabel("Name", { exact: true });
  await expect(name).toHaveValue("E2E Terms");
  await expect(page.getByRole("button", { name: "Save" })).toBeDisabled();
  await expectNoAxeViolations(page);

  await name.fill("E2E Renamed");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Name saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue("E2E Renamed");

  await page.getByRole("button", { name: "Change email" }).click();
  const dialog = page.getByRole("dialog", { name: "Change email" });
  await expect(dialog.getByLabel("New email")).toBeFocused();
  await expectNoAxeViolations(page);
});

test("email change sends a link to the new address", async ({ page }) => {
  // Sending needs email configured on the api (EMAIL_CONSOLE_FILE is enough); without it: 503.
  test.skip(process.env.E2E_EMAIL !== "1", "Set E2E_EMAIL=1 against an api with email configured.");
  await page.goto(await signUpWithWorkspace(page));
  await page.getByRole("button", { name: "Change email" }).click();
  const dialog = page.getByRole("dialog", { name: "Change email" });
  await dialog.getByLabel("New email").fill(uniqueEmail());
  await dialog.getByLabel("Password").fill("not-the-password");
  await dialog.getByRole("button", { name: "Send link" }).click();
  await expect(dialog.getByText("That password is not correct.")).toBeVisible();
  const newEmail = uniqueEmail();
  await dialog.getByLabel("New email").fill(newEmail);
  await dialog.getByLabel("Password").fill(PASSWORD);
  await dialog.getByRole("button", { name: "Send link" }).click();
  const sent = page.getByRole("dialog", { name: "Check your new inbox" });
  await expect(sent.getByRole("status")).toHaveText(
    `We sent a link to ${newEmail}. Your address changes when you open it within an hour.`,
  );
  await expectNoAxeViolations(page);
});

test("sole owner sees why delete is blocked", async ({ page }) => {
  const account = await signUpWithWorkspace(page);
  for (const theme of ["light", "dark"]) {
    await page.goto(account);
    await page.evaluate((value) => {
      window.localStorage.setItem("spanlight-theme", value);
    }, theme);
    await page.reload();
    await page.getByRole("button", { name: "Delete account" }).click();
    const dialog = page.getByRole("dialog", { name: "Delete account" });
    await expect(dialog.getByText(/^You’re the only owner of these organizations/)).toBeVisible();
    const orgs = dialog.getByRole("list", { name: "Organizations you are the only owner of" });
    await expect(orgs.getByRole("listitem")).toHaveCount(1);
    await expect(orgs).toContainText(/E2E Org/);
    await expect(orgs.getByRole("link", { name: /members$/ })).toBeVisible();
    await expect(orgs.getByRole("link", { name: /organization settings$/ })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Delete account" })).toBeDisabled();
    await expect(dialog.getByLabel("Password")).toHaveCount(0);
    await expectNoAxeViolations(page);
  }
});

test("a member can delete their account", async ({ page, browser }) => {
  const account = await signUpWithWorkspace(page);
  const invite = await createMemberInvite(page);

  const context = await browser.newContext({ baseURL: test.info().project.use.baseURL });
  const member = await context.newPage();
  const email = uniqueEmail();
  await signUp(member, email);
  await expect(member).toHaveURL(/\/onboarding/, { timeout: 15_000 });
  await member.goto(invite);
  await member.getByRole("button", { name: "Accept invite" }).click();
  await expect(member).not.toHaveURL(/\/invite/);

  await member.goto(account);
  await member.getByRole("button", { name: "Delete account" }).click();
  const dialog = member.getByRole("dialog", { name: "Delete account" });
  await expect(dialog.getByText("What is deleted")).toBeVisible();
  await expect(dialog.getByLabel("Password")).toBeFocused();
  await expect(dialog.getByRole("button", { name: "Delete account" })).toBeDisabled();
  await expectNoAxeViolations(member);

  await dialog.getByLabel("Password").fill(PASSWORD);
  await dialog.getByLabel("Type delete to confirm").fill("delete");
  await dialog.getByRole("button", { name: "Delete account" }).click();
  await expect(member).toHaveURL(/\/$/);
  await expect(member.getByText("Account deleted")).toBeVisible();

  await member.goto("/login");
  await member.getByLabel("Email").fill(email);
  await member.getByLabel("Password").fill(PASSWORD);
  await member.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(member.getByText("Invalid email or password.")).toBeVisible();
  await context.close();
});

test("confirm page shows the invalid state", async ({ page }) => {
  for (const theme of ["light", "dark"]) {
    await page.goto("/login");
    await page.evaluate((value) => {
      window.localStorage.setItem("spanlight-theme", value);
    }, theme);
    await page.goto("/confirm-email-change#token=bogus");
    await expect(page.getByRole("alert")).toHaveText(
      "This link is invalid or has expired. Request a new one in Settings › Account.",
    );
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(/Change your\s*email/);
    // The token is taken out of the address bar.
    expect(new URL(page.url()).hash).toBe("");
    await expectNoAxeViolations(page);
  }
});
