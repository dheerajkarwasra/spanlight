import { e2eEnabled, expect, expectNoAxeViolations, SKIP_REASON, test } from "./fixtures";

test.skip(!e2eEnabled, SKIP_REASON);
// The legal pages ship only in the spanlight.dev build (VITE_LEGAL_PAGES=1).
test.skip(
  process.env.E2E_LEGAL_PAGES !== "1",
  "Set E2E_LEGAL_PAGES=1 against a build or dev server started with VITE_LEGAL_PAGES=1.",
);

const PAGES = [
  ["/privacy", "Privacy policy"],
  ["/terms", "Terms of service"],
  ["/dpa", "Data processing agreement"],
  ["/subprocessors", "Subprocessors"],
  ["/imprint", "Imprint"],
  ["/accessibility", "Accessibility"],
] as const;

test.describe("legal pages", () => {
  for (const [path, heading] of PAGES) {
    test(`${path} renders and is accessible`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
      await expect(page).toHaveTitle(`${heading} · Spanlight`);
      await expectNoAxeViolations(page);
    });

    test(`${path} is accessible in the dark theme`, async ({ page }) => {
      await page.addInitScript(() => {
        window.localStorage.setItem("spanlight-theme", "dark");
      });
      await page.goto(path);
      await expect(page.locator("html")).toHaveClass(/dark/);
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
      await expectNoAxeViolations(page);
    });
  }

  test("the imprint marks the missing postal address", async ({ page }) => {
    await page.goto("/imprint");
    await expect(page.getByText("Missing: postal address")).toBeVisible();
  });

  test("a deep link opens the cookies section", async ({ page }) => {
    await page.goto("/privacy#cookies");
    await expect(
      page.getByRole("heading", { level: 2, name: "Cookies and local storage" }),
    ).toBeInViewport();
  });

  test("subprocessors lists only confirmed providers", async ({ page }) => {
    await page.goto("/subprocessors");
    await expect(page.getByRole("cell", { name: "Railway Corporation" })).toBeVisible();
    await expect(page.getByRole("cell", { name: /Sentry|Resend/ })).toHaveCount(0);
  });

  test("the landing footer links every legal page", async ({ page }) => {
    await page.goto("/");
    const footer = page.getByRole("contentinfo");
    for (const [name, href] of [
      ["Privacy", "/privacy"],
      ["Terms", "/terms"],
      ["Imprint", "/imprint"],
      ["DPA", "/dpa"],
      ["Subprocessors", "/subprocessors"],
      ["Accessibility", "/accessibility"],
    ]) {
      await expect(footer.getByRole("link", { name, exact: true })).toHaveAttribute("href", href);
    }
  });
});
