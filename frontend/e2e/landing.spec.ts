import { e2eEnabled, expect, expectNoAxeViolations, SKIP_REASON, test } from "./fixtures";

test.skip(!e2eEnabled, SKIP_REASON);

test.describe("landing page", () => {
  test("visitors see the hero, navigation and primary actions", async ({ page }) => {
    await page.goto("/");

    await expect(page).toHaveURL(/\/$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Every LLM call, accounted for." }),
    ).toBeVisible();

    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Product" })).toHaveAttribute("href", "/#see");
    await expect(nav.getByRole("link", { name: "Doctor" })).toHaveAttribute("href", "/#diagnose");
    await expect(nav.getByRole("link", { name: "Gateway" })).toHaveAttribute("href", "/#route");
    await expect(nav.getByRole("link", { name: "Self-hosting" })).toHaveAttribute("href", "/#own");

    const banner = page.getByRole("banner");
    await expect(banner.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    await expect(banner.getByRole("link", { name: "Start free" })).toHaveAttribute(
      "href",
      "/signup",
    );

    const hero = page.locator("section[aria-labelledby='hero-title']");
    await expect(hero.getByRole("link", { name: "Start free" })).toHaveAttribute("href", "/signup");
    await expect(hero.getByRole("button", { name: "Try the live demo" })).toBeVisible();
    await expect(hero.getByText('pip install "spanlight[anthropic]"')).toBeVisible();
    await expect(hero.getByRole("img", { name: /Model calls become one trace/ })).toBeVisible();
  });

  test("the product clip plays muted and can be paused", async ({ page }) => {
    // The config emulates reduced motion for every test; autoplay needs it off.
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.goto("/#see");

    const clip = page.getByRole("figure", { name: /Real traces from a demo support bot/ });
    const video = clip.locator("video");
    await expect(video).toHaveAttribute("aria-label", /repeated tool call/);
    await expect(video).toHaveAttribute("poster", "/landing/hero-poster.jpg");
    await expect(video.locator("source")).toHaveCount(2);
    await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.muted)).toBe(true);

    await clip.getByRole("button", { name: "Pause product clip" }).click();
    await expect(clip.getByRole("button", { name: "Play product clip" })).toBeVisible();
    await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.paused)).toBe(true);
  });

  test("with reduced motion the clip waits on its poster for a play click", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/#see");

    const clip = page.getByRole("figure", { name: /Real traces from a demo support bot/ });
    const video = clip.locator("video");
    await expect(clip.getByRole("button", { name: "Play product clip" })).toBeVisible();
    expect(await video.evaluate((el: HTMLVideoElement) => el.paused)).toBe(true);

    await clip.getByRole("button", { name: "Play product clip" }).click();
    await expect(clip.getByRole("button", { name: "Pause product clip" })).toBeVisible();
  });

  test("nav links scroll to their section", async ({ page }) => {
    await page.goto("/");
    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Product" })
      .click();
    await expect(page).toHaveURL(/#see$/);
    await expect(
      page.getByRole("heading", { level: 2, name: "Watch a call land." }),
    ).toBeInViewport();
  });

  test("phones get the menu sheet with the same links", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");

    await page.getByRole("button", { name: "Open menu" }).click();
    const menu = page.getByRole("dialog", { name: "Menu" });
    await expect(
      menu
        .locator("a, button")
        .filter({ hasText: /^(Start free|Try the live demo|Self-host Spanlight|Sign in)$/ }),
    ).toHaveText(["Start free", "Try the live demo", "Self-host Spanlight", "Sign in"]);
    await expect(menu.getByRole("link", { name: "Doctor" })).toBeVisible();
    await menu.getByRole("link", { name: "Doctor" }).click();

    await expect(menu).toBeHidden();
    await expect(page).toHaveURL(/#diagnose$/);
  });

  test("visitors see the seven sections in order, each with one heading", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const sections = page.locator("main > section[id]");
    await expect(sections).toHaveCount(7);
    for (const [index, id] of [
      "see",
      "price",
      "diagnose",
      "route",
      "improve",
      "start",
      "own",
    ].entries()) {
      await expect(sections.nth(index)).toHaveAttribute("id", id);
    }
    await expect(page.locator("main").getByRole("heading", { level: 2 })).toHaveText([
      "Watch a call land.",
      "Priced line by line. Checked against the bill.",
      "Sixteen detectors. No model decides.",
      "Change one base URL.",
      "Then make it better.",
      "Send your first trace.",
      "One Postgres. Yours to run.",
      "Put a light on every LLM call.",
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
    await page.goto("/#start");
    await page.getByRole("tab", { name: "Gateway", exact: true }).click();
    const copy = page.getByRole("button", { name: "Copy the Gateway snippet" });
    await expect(copy).toBeInViewport({ ratio: 1 });
    await copy.click();
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toContain('base_url="http://localhost:8080/gw/v1"');
    // 375px is the minimum width: the page itself never scrolls sideways.
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await expectNoAxeViolations(page);
  });

  test("the footer button pauses and resumes the page's motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.goto("/");
    const root = page.locator(".landing-page");
    await expect(root).toHaveAttribute("data-motion", "play");

    await page.getByRole("button", { name: "Pause motion" }).click();
    await expect(root).toHaveAttribute("data-motion", "paused");
    await expect(page.getByRole("button", { name: "Play motion" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("with reduced motion the page starts paused", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await expect(page.locator(".landing-page")).toHaveAttribute("data-motion", "paused");
    await expect(page.getByRole("button", { name: "Play motion" })).toBeVisible();
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

  // The landing is black, grey and white in both themes; this guards against theme leaks.
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
