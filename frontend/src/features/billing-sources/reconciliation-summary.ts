import type { VarianceTone } from "@/features/traces";
import type { ReconciliationDay, ReconciliationReport } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { REASONS, varianceFigure } from "./reconciliation-figures";
import {
  formatSignedUsd,
  formatUsd,
  formatVariance,
  isZeroMoney,
  sumMoney,
} from "./reconciliation-format";
import { shortDay, windowDays } from "./window-picker";

/**
 * The summary tiles of the reconciliation page (Figma "Bet1/Summary tile"; dataviz stat tiles, no
 * chart): sums over the days whose figure is known, each with a sentence saying what it covers.
 */

export type SummaryTileKey = "provider" | "spanlight" | "explained" | "residual" | "settled";

export interface SummaryTile {
  key: SummaryTileKey;
  label: string;
  /** Null when unknown: the tile shows "—" and `reason`. */
  value: string | null;
  reason: string | null;
  detail: string;
  /** What the figure means, behind the info button; null for the self-explanatory tiles. */
  info: string | null;
  /** The variance band under the residual. */
  badge: { text: string; tone: VarianceTone; accessibleName: string } | null;
}

export const TILE_INFO = {
  explained:
    "Amounts Spanlight can name for part of the gap: server-tool fees, batch spend it didn’t trace, and priority calls billed under a commitment (negative). Lines it can count but not price stay in the residual.",
  residual:
    "Provider − Spanlight − explained, signed, over the days that have both sides. Variance is the residual ÷ provider: within 2 % is the target, 2–10 % and over 10 % are worth a look.",
  settled:
    "A day settles 48 h after it ends, once a fetch has run past that point. Only settled, complete days count toward “within 2 %”.",
} as const;

const EXPLAINED_CAPTION = "Fees, batch spend and priority calls Spanlight can name (signed)";
const RESIDUAL_CAPTION = "Provider − Spanlight − explained";

type MoneyKey = "provider_usd" | "spanlight_usd" | "explained_usd" | "residual_usd";

/** The days whose figure under `key` is known. */
function known(days: readonly ReconciliationDay[], key: MoneyKey): ReconciliationDay[] {
  return days.filter((day) => day[key] !== null);
}

/** The exact sum of the known figures under `key`. */
function total(days: readonly ReconciliationDay[], key: MoneyKey): string {
  return sumMoney(days.map((day) => day[key] ?? "0"));
}

function tile(
  key: SummaryTileKey,
  label: string,
  value: string | null,
  detail: string,
): SummaryTile {
  return { key, label, value, reason: null, detail, info: null, badge: null };
}

function providerTile(report: ReconciliationReport): SummaryTile {
  const days = report.days;
  const fetched = known(days, "provider_usd");
  if (fetched.length === 0) {
    return {
      ...tile("provider", "Provider USD", null, "Not fetched yet"),
      reason: REASONS.notFetched,
    };
  }
  const value = formatUsd(total(fetched, "provider_usd"));
  if (fetched.length < days.length) {
    return tile(
      "provider",
      "Provider USD",
      value,
      `${String(fetched.length)} of ${pluralize(days.length, "day")} fetched`,
    );
  }
  const { first, last, count } = windowDays({ from: report.window.start, to: report.window.end });
  const range = first === last ? shortDay(first) : `${shortDay(first)} – ${shortDay(last)}`;
  return tile("provider", "Provider USD", value, `${pluralize(count, "day")} · ${range}`);
}

function spanlightTile(report: ReconciliationReport): SummaryTile {
  const days = report.days;
  const priced = known(days, "spanlight_usd");
  const pending = days.filter((day) => day.note === "rollup pending").length;
  if (priced.length === 0) {
    const reason = pending > 0 ? REASONS.rollupPending : REASONS.noPrice;
    return {
      ...tile(
        "spanlight",
        "Spanlight USD",
        null,
        pending > 0 ? "Rollup pending" : "No priced calls",
      ),
      reason,
    };
  }
  const value = formatUsd(total(priced, "spanlight_usd"));
  const fetched = known(days, "provider_usd");
  let detail = `From stored spans, ${pluralize(report.projects.length, "project")}`;
  if (fetched.length > 0 && fetched.length < days.length) {
    const onFetched = formatUsd(total(known(fetched, "spanlight_usd"), "spanlight_usd"));
    detail = `${pluralize(days.length, "day")}; ${onFetched} on fetched days`;
  } else if (pending > 0) {
    detail = `Rollup pending for ${pluralize(pending, "day")}`;
  }
  return tile("spanlight", "Spanlight USD", value, detail);
}

function explainedTile(report: ReconciliationReport): SummaryTile {
  const days = known(report.days, "explained_usd");
  if (days.length === 0) {
    return {
      ...tile("explained", "Explained", null, "Needs provider data"),
      reason: REASONS.notFetched,
      info: TILE_INFO.explained,
    };
  }
  const sum = total(days, "explained_usd");
  const detail = isZeroMoney(sum) ? "Nothing with a USD figure" : EXPLAINED_CAPTION;
  return { ...tile("explained", "Explained", formatUsd(sum), detail), info: TILE_INFO.explained };
}

function residualTile(report: ReconciliationReport): SummaryTile {
  const days = known(report.days, "residual_usd");
  if (days.length === 0) {
    const badge = {
      text: "No provider data",
      tone: "unknown" as const,
      accessibleName: "Variance unknown: no provider data",
    };
    return {
      ...tile("residual", "Residual", null, RESIDUAL_CAPTION),
      reason: REASONS.notFetched,
      info: TILE_INFO.residual,
      badge,
    };
  }
  const residual = total(days, "residual_usd");
  const provider = Number(total(days, "provider_usd"));
  const ratio = provider > 0 ? (Number(residual) / provider).toFixed(6) : null;
  const variance = varianceFigure(ratio, REASONS.noProviderAmount);
  const badge =
    variance.text === null || variance.band === null || variance.accessibleName === null
      ? null
      : {
          text: `${variance.text} · ${variance.band}`,
          tone: variance.tone,
          accessibleName: variance.accessibleName,
        };
  const missing = report.days.filter((day) => day.residual_usd === null);
  let detail = RESIDUAL_CAPTION;
  if (missing.length > 0) {
    const allUnfetched = missing.every((day) => day.fetched_at === null);
    detail = `Over the ${pluralize(days.length, allUnfetched ? "fetched day" : "day with both sides", allUnfetched ? "fetched days" : "days with both sides")}`;
  }
  return {
    ...tile("residual", "Residual", formatSignedUsd(residual), detail),
    info: TILE_INFO.residual,
    badge,
  };
}

function settledTile(report: ReconciliationReport): SummaryTile {
  const { days, settled_days: settled, days_within_2pct: within } = report.summary;
  let detail = "No day is settled yet";
  if (settled > 0) {
    const largest = report.summary.max_abs_variance_ratio;
    detail = `${String(within)} within 2 %`;
    if (largest !== null) {
      detail += ` · largest ${formatVariance(largest).replace(/^[+−]/, "")}`;
    }
  }
  return {
    ...tile("settled", "Settled days", `${String(settled)} of ${String(days)}`, detail),
    info: TILE_INFO.settled,
  };
}

/** Provider, Spanlight, explained, residual (with its band) and settled days. */
export function summaryTiles(report: ReconciliationReport): SummaryTile[] {
  return [
    providerTile(report),
    spanlightTile(report),
    explainedTile(report),
    residualTile(report),
    settledTile(report),
  ];
}
