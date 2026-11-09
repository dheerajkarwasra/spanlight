import { e2eEnabled, SKIP_REASON } from "./fixtures";
import { createWorkspace, expect, sendTrace, test } from "./workspace";

/*
 * Live dashboards: with the Overview already open on a new project, one trace sent through
 * `POST /v1/traces` shows up as 1 call within 5 s, without a reload.
 */

test.describe("live overview", () => {
  test.skip(!e2eEnabled, SKIP_REASON);

  test("shows a new trace's call within 5 s without a reload", async ({ page }) => {
    const { apiKey } = await createWorkspace(page, { apiKey: true });
    if (!apiKey) throw new Error("the workspace has no API key");

    const calls = page
      .getByRole("list", { name: "Key metrics" })
      .getByRole("listitem")
      .filter({ hasText: "Calls · error rate" })
      .locator(".text-metric");
    // The page has loaded (first run: nothing yet) before the trace is sent.
    await expect(calls).toBeVisible();
    await expect(calls).not.toHaveText("1");
    // A reload would drop this marker.
    await page.evaluate(() => {
      (window as unknown as { liveMarker?: boolean }).liveMarker = true;
    });

    const sentAt = Date.now();
    await sendTrace(page, apiKey, { name: "live-overview" });
    await expect(calls).toHaveText("1", { timeout: 5_000 });
    test.info().annotations.push({
      type: "live-latency-ms",
      description: String(Date.now() - sentAt),
    });

    expect(
      await page.evaluate(() => (window as unknown as { liveMarker?: boolean }).liveMarker),
    ).toBe(true);
  });
});
