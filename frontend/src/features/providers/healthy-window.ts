import type { HealthReport } from "@/lib/api";

/** No provider failed a call in the window, so the failure breakdowns have nothing to show. */
export function isHealthyWindow(report: HealthReport): boolean {
  return report.providers.length > 0 && report.providers.every((provider) => provider.errors === 0);
}
