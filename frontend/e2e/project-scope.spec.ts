import { e2eEnabled, expect, SKIP_REASON, test, uniqueEmail } from "./fixtures";
import { createWorkspace, sendTrace } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
test.use({ trace: "off", screenshot: "off", video: "off" });
test.afterEach(async ({ page }) => {
  await page.close();
});

test("delayed project reads never carry previous metrics, traces, sessions or selections", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/signup");
  await page.getByLabel("Name").fill("S3 User");
  await page.getByLabel("Work email").fill(uniqueEmail());
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/onboarding/);
  const { base: baseA, apiKey } = await createWorkspace(page, { apiKey: true });
  expect(apiKey).toBeTruthy();
  await sendTrace(page, apiKey ?? "", {
    name: "S3 private trace",
    sessionId: "S3-private-session",
    endTime: new Date(Date.now() - 120_000),
  });
  const me = (await (await page.request.get("/api/v1/auth/me")).json()) as {
    memberships: { org: { id: string } }[];
  };
  const orgId = me.memberships[0]?.org.id;
  expect(orgId).toBeTruthy();
  await page.goto(`/onboarding?org=${orgId}`);
  await page.getByLabel("Project name").fill("S3 Empty");
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" && response.url().endsWith(`/orgs/${orgId}/projects`),
  );
  await page.getByRole("button", { name: "Create project" }).click();
  const projectB = (await (await created).json()) as { id: string };
  await page.getByRole("link", { name: "Skip to dashboard" }).click();
  await expect(page).toHaveURL(/overview$/);
  const baseB = new URL(page.url()).pathname.replace(/\/overview$/, "");

  // Use the app's real client router so observers stay mounted during a same-route project switch.
  async function navigate(to: string) {
    await page.evaluate(async (href) => {
      const router = (
        window as unknown as {
          __TSR_ROUTER__: { navigate(options: { to: string }): Promise<void> };
        }
      ).__TSR_ROUTER__;
      await router.navigate({ to: href });
    }, to);
  }

  for (const surface of ["overview", "traces", "sessions"] as const) {
    await page.goto(`${baseA}/${surface}`);
    if (surface === "overview")
      await expect(page.getByText("Loading overview", { exact: true })).toHaveCount(0);
    if (surface === "traces") {
      await page.getByRole("checkbox", { name: /Select S3 private trace/ }).check();
      await expect(page.getByRole("region", { name: "Selected traces" })).toBeVisible();
      await page.getByRole("link", { name: "S3 private trace", exact: true }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
    }
    if (surface === "sessions")
      await expect(
        page
          .getByRole("table", { name: "Sessions" })
          .getByRole("link", { name: "S3-private-session", exact: true }),
      ).toBeVisible();
    let release!: () => void;
    let requestStarted = false;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const pattern = `**/api/v1/projects/${projectB.id}/${surface === "overview" ? "metrics/overview" : surface}?*`;
    await page.route(pattern, async (route) => {
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      requestStarted = true;
      await held;
      await route.fulfill({ response });
    });
    await navigate(`${baseB}/${surface}`);
    await expect.poll(() => requestStarted).toBe(true);
    await expect(
      page
        .getByRole("button", { name: "Project S3 Empty. Switch project or organization" })
        .first(),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "S3 private trace", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "S3-private-session", exact: true })).toHaveCount(
      0,
    );
    await expect(page.getByRole("region", { name: "Selected traces" })).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    if (surface === "overview")
      await expect(page.getByText("Loading overview", { exact: true })).toHaveCount(1);
    await navigate(`${baseA}/${surface}`);
    release();
    await page.unrouteAll({ behavior: "wait" });
    await expect(
      page
        .getByRole("button", { name: "Project E2E Project. Switch project or organization" })
        .first(),
    ).toBeVisible();
    if (surface === "traces") {
      await expect(page.getByRole("link", { name: "S3 private trace", exact: true })).toBeVisible();
      await expect(page.getByRole("region", { name: "Selected traces" })).toHaveCount(0);
    }
  }
  await page
    .getByRole("button", { name: /Account menu/ })
    .first()
    .click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/login/);
  await expect(page.getByRole("link", { name: "S3-private-session", exact: true })).toHaveCount(0);
});
