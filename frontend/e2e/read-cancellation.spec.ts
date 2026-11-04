import { e2eEnabled, expect, SKIP_REASON, test, uniqueEmail } from "./fixtures";
import { createWorkspace, sendTrace } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
test.use({ trace: "off", screenshot: "off", video: "off" });
test.afterEach(async ({ page }) => {
  await page.close();
});

test("abandoned reads abort on navigation, filter replacement and project switch", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    const aborted: string[] = [];
    const rejected: string[] = [];
    Object.assign(window, { readAborts: aborted, readRejections: rejected });
    window.fetch = async (input, options) => {
      options?.signal?.addEventListener(
        "abort",
        () =>
          aborted.push(
            typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
          ),
        { once: true },
      );
      try {
        return await nativeFetch(input, options);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError")
          rejected.push(
            typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
          );
        throw error;
      }
    };
  });
  await page.goto("/signup");
  await page.getByLabel("Name").fill("S13 User");
  await page.getByLabel("Work email").fill(uniqueEmail());
  await page.getByLabel("Password").fill("correct-horse-battery");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/onboarding/);
  const { base: baseA, apiKey } = await createWorkspace(page, { apiKey: true });
  await sendTrace(page, apiKey ?? "", {
    name: "S13 current",
    sessionId: "S13-session",
    endTime: new Date(Date.now() - 120_000),
  });
  const me = (await (await page.request.get("/api/v1/auth/me")).json()) as {
    memberships: { org: { id: string } }[];
  };
  const orgId = me.memberships[0]?.org.id;
  if (!orgId) throw new Error("Signup did not create an organization");
  await page.goto(`/onboarding?org=${orgId}`);
  await page.getByLabel("Project name").fill("S13 Empty");
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name: "Skip to dashboard" }).click();
  await expect(page).toHaveURL(/overview$/);
  const baseB = new URL(page.url()).pathname.replace(/\/overview$/, "");
  await page.goto(`${baseA}/traces`);
  await expect(page.getByRole("link", { name: "S13 current", exact: true })).toBeVisible();
  async function navigate(to: string) {
    await page.evaluate(async (href) => {
      await (
        window as unknown as {
          __TSR_ROUTER__: { navigate(options: { to: string }): Promise<void> };
        }
      ).__TSR_ROUTER__.navigate({ to: href });
    }, to);
  }
  async function hold(pattern: string) {
    let release!: () => void;
    let started = false;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(pattern, async (route) => {
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      started = true;
      await gate;
      // The browser may already have abandoned the intercepted request.
      await route.fulfill({ response }).catch(() => undefined);
    });
    return { release, started: () => started };
  }
  async function expectAbort(fragment: string) {
    await expect
      .poll(() =>
        page.evaluate((part) => {
          const evidence = window as unknown as { readAborts: string[]; readRejections: string[] };
          return (
            evidence.readAborts.some((url) => url.includes(part)) &&
            evidence.readRejections.some((url) => url.includes(part))
          );
        }, fragment),
      )
      .toBe(true);
  }
  const navigation = await hold("**/traces?*q=S13-navigation*");
  await page.getByRole("searchbox", { name: "Search traces" }).fill("S13-navigation");
  await expect.poll(navigation.started).toBe(true);
  await navigate(`${baseA}/sessions`);
  await expectAbort("q=S13-navigation");
  navigation.release();
  await page.unrouteAll({ behavior: "wait" });
  await expect(page.getByRole("link", { name: "S13-session", exact: true })).toBeVisible();

  await navigate(`${baseA}/traces`);
  const filter = await hold("**/traces?*q=S13-old*");
  await page.getByRole("searchbox", { name: "Search traces" }).fill("S13-old");
  await expect.poll(filter.started).toBe(true);
  await page.getByRole("searchbox", { name: "Search traces" }).fill("S13 current");
  await expectAbort("q=S13-old");
  await expect(page.getByRole("link", { name: "S13 current", exact: true })).toBeVisible();
  filter.release();
  await page.unrouteAll({ behavior: "wait" });
  await expect(page.getByRole("link", { name: "S13 current", exact: true })).toBeVisible();

  const project = await hold("**/metrics/overview?*");
  await navigate(`${baseA}/overview`);
  await expect.poll(project.started).toBe(true);
  await navigate(`${baseB}/traces`);
  await expectAbort("/metrics/overview?");
  await expect(
    page.getByRole("button", { name: "Project S13 Empty. Switch project or organization" }).first(),
  ).toBeVisible();
  project.release();
  await page.unrouteAll({ behavior: "wait" });
  await expect(page.getByRole("link", { name: "S13 current", exact: true })).toHaveCount(0);
});
