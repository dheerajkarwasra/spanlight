import { DB_SKIP_REASON, dbEnabled, insertProviderIncidentInsight, projectIdOf } from "./db-seed";
import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { checkAtWidths, createWorkspace, expect, sendTrace, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

test("doctor, releases and users: honest empty states, then a release comparison", async ({
  page,
}) => {
  test.setTimeout(240_000);
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });

  // 1. Empty states on a fresh project.
  await page.goto(`${base}/doctor`);
  await expect(page.getByRole("heading", { name: /No findings yet|Nothing open/ })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.goto(`${base}/releases`);
  await expect(page.getByRole("heading", { name: "No releases in this window" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.goto(`${base}/users`);
  await expect(page.getByRole("heading", { name: "No end users in this window" })).toBeVisible();
  await expectNoAxeViolations(page);

  // 2. Spans tagged with a release and an end user.
  for (const release of ["v1", "v2"]) {
    await sendTrace(page, apiKey ?? "", { release, userId: "user-ada", name: `chat-${release}` });
  }
  await sendTrace(page, apiKey ?? "", { release: "v2", userId: "user-bob", status: "error" });

  // 3. The releases list is read live from traces: compare v1 with v2.
  await page.goto(`${base}/releases`);
  await expect(page.getByRole("heading", { name: "Compare releases" })).toBeVisible();
  await page.getByRole("combobox", { name: "Baseline (A)" }).click();
  await page.getByRole("option", { name: /^v1/ }).click();
  await page.getByRole("combobox", { name: "Candidate (B)" }).click();
  await page.getByRole("option", { name: /^v2/ }).click();
  await page.getByRole("button", { name: "Compare" }).click();
  const metrics = page.getByRole("table", { name: "Key metrics of both releases" });
  await expect(metrics).toBeVisible();
  await expect(metrics.getByRole("row", { name: /Traces/ })).toContainText("1");
  await expect(metrics.getByRole("row", { name: /Error rate/ })).toContainText("50.0%");
  await expectNoAxeViolations(page);

  // 4. The trace list shows the end user on the spans just sent.
  await page.goto(`${base}/traces`);
  await expect(page.getByRole("link", { name: "chat-v1" })).toBeVisible();
  await expectNoAxeViolations(page);
});

// The insight is written to the database (a detector needs minutes of traffic to open one); its
// cited status page incident has a title that would run script if it were rendered as HTML.
const HOSTILE_INCIDENT_TITLE = "<script>window.__e2eXss = 1</script> E2E elevated errors";

test("doctor feedback: a verdict is set, kept, cleared; the Precision tab follows", async ({
  page,
}) => {
  test.skip(!dbEnabled, DB_SKIP_REASON);
  test.setTimeout(240_000);
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });
  const traceId = await sendTrace(page, apiKey ?? "", {
    name: "overloaded call",
    provider: "anthropic",
    status: "error",
    statusMessage: "Overloaded",
    attributes: { "http.status_code": 529, "provider.error.type": "overloaded_error" },
  });
  const insightId = insertProviderIncidentInsight(
    projectIdOf(base),
    [traceId],
    HOSTILE_INCIDENT_TITLE,
  );

  // 1. The detail page: the incident line shows the title as text.
  await page.goto(`${base}/doctor/${insightId}`);
  await expect(
    page.getByRole("heading", { level: 1, name: "anthropic is failing across your organization" }),
  ).toBeVisible();
  const citation = page.getByRole("link", {
    name: `${HOSTILE_INCIDENT_TITLE} (opens in a new tab)`,
    exact: true,
  });
  await expect(citation).toBeVisible();
  await expect(citation.locator("script")).toHaveCount(0);
  expect(
    await page.evaluate(() => (window as unknown as { __e2eXss?: unknown }).__e2eXss),
  ).toBeUndefined();
  const feedback = page.getByRole("region", { name: "Was this right?" });
  const right = feedback.getByRole("button", { name: "Right" });
  const wrong = feedback.getByRole("button", { name: "Wrong" });
  await expect(right).toHaveAttribute("aria-pressed", "false");
  await checkAtWidths(page, "insight-detail", async () => {
    await expect(right).toBeVisible();
  });

  // 2. Right saves at once and opens the optional note; Skip keeps the vote, and so does a reload.
  await right.click();
  await expect(right).toHaveAttribute("aria-pressed", "true");
  await expect(feedback).toContainText("Saved: you said this was right.");
  await feedback.getByRole("button", { name: "Skip" }).click();
  await page.reload();
  await expect(right).toHaveAttribute("aria-pressed", "true");
  await expect(wrong).toHaveAttribute("aria-pressed", "false");
  await expect(feedback).toContainText("You said this was right on");
  await checkAtWidths(page, "insight-detail-voted", async () => {
    await expect(right).toBeVisible();
  });

  // 3. The Precision tab counts the answer.
  await page.goto(`${base}/doctor`);
  await page.getByRole("radio", { name: "Precision" }).click();
  await expect(page.getByRole("heading", { name: "How often each check is right" })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "Provider incident" })).toBeVisible();
  await checkAtWidths(page, "doctor-precision", async () => {
    // The table on a laptop, one card per check on a phone.
    await expect(
      page.getByText("Provider incident", { exact: true }).filter({ visible: true }),
    ).toHaveCount(1);
  });

  // 4. Clicking the pressed verdict takes the vote back; the Precision tab empties.
  await page.goto(`${base}/doctor/${insightId}`);
  await expect(right).toHaveAttribute("aria-pressed", "true");
  await right.click();
  await expect(right).toHaveAttribute("aria-pressed", "false");
  await expect(feedback).toContainText("Your answer measures how often this check is right.");
  await page.reload();
  await expect(right).toHaveAttribute("aria-pressed", "false");
  await page.goto(`${base}/doctor?tab=precision`);
  await expect(page.getByRole("heading", { name: "No answers yet" })).toBeVisible();
  await expectNoAxeViolations(page);
});
