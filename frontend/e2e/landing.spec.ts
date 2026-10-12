import { e2eEnabled, expect, expectNoAxeViolations, SKIP_REASON, test } from "./fixtures";

test.skip(!e2eEnabled, SKIP_REASON);

test.describe("landing page", () => {
  test("visitors see the hero, navigation and primary actions", async ({ page }) => {
    await page.goto("/");

    await expect(page).toHaveURL(/\/$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "See each LLM call clearly." }),
    ).toBeVisible();

    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Features" })).toHaveAttribute("href", "#features");
    await expect(nav.getByRole("link", { name: "How it works" })).toHaveAttribute(
      "href",
      "#how-it-works",
    );
    await expect(nav.getByRole("link", { name: "Open source" })).toHaveAttribute(
      "href",
      "#open-source",
    );

    await expect(page.getByRole("link", { name: "Get started — it’s free" })).toHaveAttribute(
      "href",
      "/signup",
    );
    await expect(page.getByRole("banner").getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/login",
    );
    await expect(page.getByRole("button", { name: "Try the live demo" }).first()).toBeVisible();
  });

  test("nav links scroll to their section", async ({ page }) => {
    await page.goto("/");
    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Features" })
      .click();
    await expect(page).toHaveURL(/#features$/);
    await expect(
      page.getByRole("heading", { level: 2, name: /Everything you need/ }),
    ).toBeInViewport();
  });

  test("phones get the menu sheet with the same links", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");

    await page.getByRole("button", { name: "Open menu" }).click();
    const menu = page.getByRole("dialog", { name: "Menu" });
    await expect(menu.getByRole("link", { name: "How it works" })).toBeVisible();
    await menu.getByRole("link", { name: "How it works" }).click();

    await expect(menu).toBeHidden();
    await expect(page).toHaveURL(/#how-it-works$/);
  });

  test("the header and footer link the docs, the repository and PyPI", async ({ page }) => {
    await page.goto("/");

    const header = page.getByRole("banner");
    await expect(header.getByRole("link", { name: "Docs" })).toHaveAttribute(
      "href",
      "/docs/quickstart/",
    );
    await expect(header.getByRole("link", { name: "Spanlight on GitHub" })).toHaveAttribute(
      "href",
      /^https:\/\/github\.com\/[^/]+\/spanlight$/,
    );

    const footer = page.getByRole("navigation", { name: "Footer" });
    await expect(footer.getByRole("link", { name: "Evaluators" })).toHaveAttribute(
      "href",
      "/docs/evaluators/",
    );
    await expect(footer.getByRole("link", { name: "Experiments" })).toHaveAttribute(
      "href",
      "/docs/experiments/",
    );
    await expect(footer.getByRole("link", { name: "Eval gate (CI)" })).toHaveAttribute(
      "href",
      "/docs/eval-gate/",
    );
    await expect(footer.getByRole("link", { name: "PyPI package" })).toHaveAttribute(
      "href",
      "https://pypi.org/project/spanlight/",
    );

    const contentinfo = page.getByRole("contentinfo");
    await expect(contentinfo.getByRole("link", { name: "Report an issue" })).toHaveAttribute(
      "href",
      /\/spanlight\/issues$/,
    );
    await expect(contentinfo.getByRole("link", { name: "Security policy" })).toHaveAttribute(
      "href",
      "/docs/security/policy/",
    );
  });

  test("the landing page is accessible", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("the landing page is accessible in the dark theme", async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem("spanlight-theme", "dark");
    });
    await page.goto("/");
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectNoAxeViolations(page);
  });
});
