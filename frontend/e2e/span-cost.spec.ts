import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { createWorkspace, expect, sendTrace, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 180_000 });

// A priced span: Claude Sonnet 4.6 with its usage, a billed tier and two web searches. The card
// recomputes the stored cost from that usage and the price row, and the two agree.
test("a priced span's cost breakdown matches its stored cost", async ({ page }) => {
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });
  const traceId = await sendTrace(page, apiKey ?? "", {
    name: "priced-call",
    provider: "anthropic",
    model: "claude-sonnet-4-6",
    usage: {
      input_tokens: 1_200,
      output_tokens: 300,
      service_tier: "standard",
      server_tool_use: { web_search_requests: 2 },
    },
  });

  await page.goto(`${base}/traces/${traceId}`);
  const heading = page.getByRole("heading", { name: "Cost breakdown" });
  await expect(heading).toBeVisible();
  await page.getByRole("button", { name: "Show cost breakdown" }).click();
  const body = page.getByTestId("span-cost-body");
  await expect(body.getByText(/^Recomputed from stored usage and price row/)).toBeVisible();
  // The card header: the stored cost and its status. $3 / $15 per million tokens and $10 per
  // 1,000 searches: 1,200 input $0.0036 + 300 output $0.0045 + 2 searches $0.02 = $0.0281.
  const header = heading.locator("..");
  await expect(header).toContainText("$0.028100");
  await expect(header.getByText("Matches", { exact: true })).toBeVisible();
  // The web search fee is its own priced line.
  const fee = body.getByRole("row", { name: /^Web search/ });
  await expect(fee.getByRole("cell")).toHaveText(["2", "$10.00 / 1K requests", "", "$0.020000"]);
  await expect(page.getByRole("button", { name: "Hide cost breakdown" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await expectNoAxeViolations(page);
});
