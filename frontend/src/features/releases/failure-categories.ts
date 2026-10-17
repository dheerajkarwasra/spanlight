/**
 * The Releases "Failure categories" card's numbers: one point per category per release, the table
 * twin, and which empty state applies. Pure functions only.
 */
import type { ChartDataTable } from "@/components/chart-data-table";
import { isApiError, type FailureCategoryRates } from "@/lib/api";
import { formatInteger, formatPercent } from "@/lib/format";

/** Below this many reviewed traces a release's intervals are too wide to read much into. */
export const FEW_ITEMS = 10;

/** The category a reviewed trace that passed carries: in the table, left out of the chart. */
export const NONE_CATEGORY = "none";

/** Releases the chart draws, each in its own hue; the table lists the rest. */
export const CHART_RELEASE_LIMIT = 3;

/** How a release without one is named on the card. */
export const NO_RELEASE_LABEL = "No release";

export interface FailureCategoryPoint {
  release: string;
  /** The category. */
  label: string;
  count: number;
  /** `null` when the release has no reviewed trace (unknown, never 0%). */
  rate: number | null;
  /** The Wilson 95% bounds of `rate`. */
  low: number | null;
  high: number | null;
  reviewed: number;
}

function releaseLabel(release: string | null): string {
  return release ?? NO_RELEASE_LABEL;
}

/** The config's categories in its order, then any label no longer in it, as the API lists them. */
export function categoriesOf(data: FailureCategoryRates): string[] {
  const all = [...data.categories];
  for (const release of data.releases) {
    for (const name of Object.keys(release.rates)) {
      if (!all.includes(name)) {
        all.push(name);
      }
    }
  }
  return all;
}

/** One point per category per release, releases in the API's order (the one seen last first). */
export function chartSeries(data: FailureCategoryRates): FailureCategoryPoint[] {
  const categories = categoriesOf(data);
  return data.releases.flatMap((release) =>
    categories.map((label) => {
      const rate = release.reviewed > 0 ? release.rates[label] : undefined;
      return {
        release: releaseLabel(release.release),
        label,
        count: rate?.count ?? 0,
        rate: rate ? Number(rate.rate) : null,
        low: rate ? Number(rate.ci_low) : null,
        high: rate ? Number(rate.ci_high) : null,
        reviewed: release.reviewed,
      };
    }),
  );
}

/** "7 (14.6%, 7.3–27.2%)"; "—" when the release has no reviewed trace. */
export function pointText(point: FailureCategoryPoint): string {
  if (point.rate === null) {
    return "—";
  }
  return `${formatInteger(point.count) ?? "0"} (${formatPercent(point.rate) ?? "—"}, ${intervalText(point)})`;
}

/** "7.3–27.2%"; "—" without an interval. */
export function intervalText(point: FailureCategoryPoint): string {
  if (point.low === null || point.high === null) {
    return "—";
  }
  const low = (formatPercent(point.low) ?? "").replace("%", "");
  return `${low}–${formatPercent(point.high) ?? ""}`;
}

/** The chart's numbers as a table: a row per release, a column per category. */
export function dataTable(data: FailureCategoryRates): ChartDataTable {
  const categories = categoriesOf(data);
  const points = chartSeries(data);
  return {
    caption: "Failure categories per release: count, rate and 95% interval of reviewed traces",
    columns: ["Release", "Reviewed", ...categories],
    rows: data.releases.map((release, index) => ({
      key: release.release ?? "",
      cells: [
        releaseLabel(release.release),
        release.reviewed > 0 ? (formatInteger(release.reviewed) ?? "0") : "—",
        ...points.slice(index * categories.length, (index + 1) * categories.length).map(pointText),
      ],
    })),
  };
}

/** Too few reviewed traces for a narrow interval. */
export function fewItems(reviewed: number): boolean {
  return reviewed < FEW_ITEMS;
}

/**
 * `no_categories` when the project has no `failure_category` config yet (404 NOT_FOUND),
 * `no_reviewed` when no release in the window has a reviewed trace, otherwise null.
 */
export function emptyState(
  data: FailureCategoryRates | undefined,
  error: unknown,
): "no_categories" | "no_reviewed" | null {
  if (isApiError(error) && error.status === 404 && error.code === "NOT_FOUND") {
    return "no_categories";
  }
  if (data !== undefined && data.releases.every((release) => release.reviewed === 0)) {
    return "no_reviewed";
  }
  return null;
}
