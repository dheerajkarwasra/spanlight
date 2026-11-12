import { spawn } from "node:child_process";

import {
  e2eEnabled,
  expect,
  expectNoAxeViolations,
  SKIP_REASON,
  test,
  uniqueEmail,
} from "./fixtures";

test.skip(!e2eEnabled, SKIP_REASON);
// The newly revealed key and copied snippets must stay in memory, including on failure.
test.use({ trace: "off", screenshot: "off", video: "off" });
// Close before Playwright builds an automatic failure accessibility snapshot containing the key.
test.afterEach(async ({ page }) => {
  await page.close();
});

async function runSnippet(command: string, args: string[], code: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["pipe", "pipe", "pipe"], env: process.env });
    let output = "";
    const timer = setTimeout(() => child.kill("SIGKILL"), 20_000);
    child.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString();
    });
    child.stderr.resume();
    child.on("error", () => {
      clearTimeout(timer);
      reject(new Error("Snippet process could not start"));
    });
    child.on("close", (status) => {
      clearTimeout(timer);
      if (status === 0) resolve(output);
      else reject(new Error("Snippet process failed"));
    });
    child.stdin.end(code);
  });
}

test("setup traffic verifies ingestion; only an instrumented app completes onboarding", async ({
  page,
  context,
}) => {
  test.setTimeout(120_000);
  const capture = async (name: string) => {
    if (process.env.E2E_VISUAL_DIR) {
      await page.screenshot({
        path: `${process.env.E2E_VISUAL_DIR}/${name}.png`,
        fullPage: true,
        style: "code { visibility: hidden !important; }",
      });
    }
  };
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/signup");
  await page.getByLabel("Name").fill("E2E User");
  await page.getByLabel("Work email").fill(uniqueEmail());
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery");
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page).toHaveURL(/\/onboarding/);
  await page.getByLabel("Organization name").fill("E2E Org");
  const createOrg = page.getByRole("button", { name: "Continue" });
  await expect(createOrg).toHaveAccessibleDescription("Creates your organization");
  await createOrg.click();
  await expect(page.getByRole("heading", { level: 1, name: "E2E Org is ready." })).toBeVisible();
  await page.getByLabel("Project name").fill("E2E Project");
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("button", { name: "Create API key" }).click();
  await expect(page.getByRole("button", { name: "Copy API key" })).toBeVisible();
  await expect(page.getByText("Shown once. Revoke it in Settings › API keys.")).toBeVisible();
  await expect(page.getByText("Waiting for an application trace…")).toBeVisible();
  await expectNoAxeViolations(page);

  await capture("waiting-desktop");
  const projectId = new URL(page.url()).searchParams.get("project");
  expect(projectId).toBeTruthy();
  const projectApi = `/api/v1/projects/${projectId}`;

  // Execute the actual copied curl snippet against the real ingestion endpoint.
  await page.getByRole("tab", { name: "curl" }).click();
  await page.getByRole("button", { name: "Copy curl: Send a test span" }).click();
  const curl = await page.evaluate(() => navigator.clipboard.readText());
  const result = JSON.parse(await runSnippet("sh", [], curl)) as {
    accepted: number;
    rejected: unknown[];
  };
  expect(result).toMatchObject({ accepted: 1, rejected: [] });
  await expect(page.getByText("Test trace received")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Ingestion works. Instrument your app to connect it.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Your app is connected" })).toHaveCount(0);
  const setupActivation = await page.request.get(`${projectApi}/activation`);
  expect(await setupActivation.json()).toMatchObject({
    first_trace: false,
    first_trace_id: null,
    first_trace_at: null,
  });
  const from = new Date(Date.now() - 60_000).toISOString();
  const to = new Date(Date.now() + 60_000).toISOString();
  const tracesResponse = await page.request.get(`${projectApi}/traces`, { params: { from, to } });
  const traces = (await tracesResponse.json()) as {
    items: { trace_id: string; cost_usd: string | null; tags: string[] }[];
  };
  expect(traces.items).toHaveLength(1);
  expect(traces.items[0]).toMatchObject({ cost_usd: null, tags: ["spanlight:setup-test"] });
  const setupDetail = await page.request.get(`${projectApi}/traces/${traces.items[0]?.trace_id}`);
  const setup = (await setupDetail.json()) as { spans: unknown[] };
  expect(setup.spans[0]).toMatchObject({
    kind: "chain",
    model: null,
    provider: null,
    input_tokens: null,
    output_tokens: null,
    cost_usd: null,
  });

  await capture("connectivity-desktop");
  // Disconnect the actual browser network after a successful cached response.
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await context.setOffline(true);
    await expect(page.getByRole("alert")).toContainText("Checking interrupted", {
      timeout: 20_000,
    });
    await expect(page.getByRole("alert")).toContainText("Your key and snippets are still here.");
    await expect(page.getByRole("button", { name: "Copy API key" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "curl" })).toHaveAttribute("aria-selected", "true");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await expectNoAxeViolations(page);
    await capture(`interrupted-${width}`);
    await context.setOffline(false);
    const retry = page.getByRole("button", { name: "Try again" });
    await retry.focus();
    await retry.press("Enter");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByText("Test trace received")).toBeVisible();
  }

  // The button also sends real setup traffic and must never activate the application.
  await page.getByRole("button", { name: "Send a test trace" }).click();
  await expect(page.getByRole("button", { name: "Send a test trace" })).toBeDisabled();
  expect(await (await page.request.get(`${projectApi}/activation`)).json()).toMatchObject({
    first_trace: false,
  });

  // Run the copied Python SDK instrumentation, with no provider call or fictional billing.
  await page.getByRole("tab", { name: "Python SDK" }).click();
  await page.getByRole("button", { name: "Copy Python SDK: Instrument" }).click();
  const python = await page.evaluate(() => navigator.clipboard.readText());
  await runSnippet(process.env.E2E_SDK_PYTHON ?? "python3", ["-"], python);
  await expect(page.getByRole("heading", { name: "Your app is connected" })).toBeVisible({
    timeout: 15_000,
  });
  await expect(
    page.getByText("Application trace received. Cost is unavailable for this trace."),
  ).toBeVisible();
  await expect(page.getByRole("list", { name: "First trace" })).toContainText("answer");
  await expect(page.getByRole("list", { name: "First trace" })).toContainText("Cost: —");
  await expect(page.getByRole("button", { name: "Copy API key" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expectNoAxeViolations(page);
  await capture("connected-unknown-desktop");
  await page.getByRole("link", { name: "Open your first trace" }).click();
  await expect(page).toHaveURL(/\/traces\/[0-9a-f]{32}/);
  await expect(page.getByText("You asked: What does Spanlight record?").first()).toBeVisible();
  await expectNoAxeViolations(page);
});
