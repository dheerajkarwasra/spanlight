import { e2eEnabled, expect, expectNoAxeViolations, SKIP_REASON, test } from "./fixtures";

test.skip(!e2eEnabled, SKIP_REASON);

test.describe("landing page", () => {
  test("visitors see the hero, navigation and primary actions", async ({ page }) => {
    await page.goto("/");

    await expect(page).toHaveURL(/\/$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "See every LLM call clearly." }),
    ).toBeVisible();

    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Product" })).toHaveAttribute("href", "#cost");
    await expect(nav.getByRole("link", { name: "How it works" })).toHaveAttribute(
      "href",
      "#how-it-works",
    );
    await expect(nav.getByRole("link", { name: "Self-hosting" })).toHaveAttribute(
      "href",
      "#open-source",
    );

    await expect(page.getByRole("link", { name: "Self-host Spanlight" }).first()).toHaveAttribute(
      "href",
      "/docs/self-hosting/",
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
      .getByRole("link", { name: "Product" })
      .click();
    await expect(page).toHaveURL(/#cost$/);
    await expect(
      page.getByRole("heading", { level: 2, name: /Understand the cost/ }),
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

  test("visitors see setup before the product benefits and four supporting features", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator("main > section[id]")).toHaveCount(4);
    await expect(page.locator("main > section[id]").nth(0)).toHaveAttribute("id", "how-it-works");
    await expect(page.locator("main > section[id]").nth(1)).toHaveAttribute("id", "cost");
    await expect(page.locator("#cost").getByRole("heading", { level: 3 })).toHaveText([
      "Know what every call costs",
      "Find the cause with the Doctor",
      "Checked against the bill",
    ]);
    await expect(page.locator("#diagnose").getByRole("heading", { level: 3 })).toHaveText([
      "One base URL, no SDK",
      "Evals with honest error bars",
      "Prompts, playground, replay",
      "Sessions",
    ]);
    await expect(
      page.getByRole("link", { name: "Self-hosting guide", exact: true }),
    ).toHaveAttribute("href", "/docs/self-hosting/");
  });

  test("phone visitors can switch integration tabs and copy the visible snippet", async ({
    page,
    context,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/#how-it-works");
    await page.getByRole("tab", { name: "Gateway", exact: true }).click();
    const copy = page.getByRole("button", { name: "Copy the Gateway snippet" });
    await expect(copy).toBeInViewport({ ratio: 1 });
    await copy.click();
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(
        'from openai import OpenAI\n\nclient = OpenAI(\n    base_url="…/gw/v1",\n    api_key="<YOUR_GATEWAY_KEY>",\n)',
      );
    await expectNoAxeViolations(page);
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
