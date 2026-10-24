import AxeBuilder from "@axe-core/playwright";
import { expect, test as base, type Page } from "@playwright/test";

/** E2E tests need the real stack. Set E2E_BASE_URL (e.g. http://localhost:5173) to run them. */
export const e2eEnabled = Boolean(process.env.E2E_BASE_URL);

export const SKIP_REASON = "Set E2E_BASE_URL to run end-to-end tests against a running stack.";

export const test = base;

export async function expectNoAxeViolations(page: Page): Promise<void> {
  // Toasts are transient: axe samples them mid-fade and inspects the stacked,
  // intentionally hidden toasts behind the front one. Their colors use the same
  // foreground/surface tokens as cards, which are covered elsewhere.
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .exclude("[data-sonner-toaster]")
    .analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
}

export function uniqueEmail(): string {
  return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

export { expect };
