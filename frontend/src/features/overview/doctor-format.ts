import type { DetectorRun, Health, IncidentList, Kpis } from "@/lib/api";
import {
  formatCost,
  formatDuration,
  formatInteger,
  formatPercent,
  formatRelativeTime,
  pluralNoun,
  pluralize,
} from "@/lib/format";
import { feedLabel } from "@/lib/provider-label";

import { errorCountFrom } from "./hero";

/** Shown (as the "—" tooltip) when the window has no LLM calls, so there is nothing to score. */
export const NO_SCORE_REASON = "No LLM calls in this window";

/** How the score is made: the score ring's tooltip. */
export const FORMULA_LINE = "100 minus penalties for findings, errors, latency and cost";
/** `Health.approximate`: some of the metrics behind the score come from hourly rollups. */
export const APPROXIMATE_LINE = "Approximate: some figures come from hourly rollups.";

export type HealthBand = "good" | "fair" | "poor" | "none";

export const BAND_LABELS: Record<HealthBand, string> = {
  good: "Good",
  fair: "Fair",
  poor: "Poor",
  none: "No score",
};

/** 80 and up is good, 50 to 79 fair, below that poor; `null` has no band. */
export function healthBand(value: number | null): HealthBand {
  if (value === null) {
    return "none";
  }
  if (value >= 80) {
    return "good";
  }
  return value >= 50 ? "fair" : "poor";
}

export type ComponentName = "findings" | "errors" | "latency" | "cost";

const COMPONENT_LABELS: Record<ComponentName, string> = {
  findings: "Findings",
  errors: "Errors",
  latency: "Latency",
  cost: "Cost",
};

/**
 * The most points each component can take off the score. Mirrors FINDINGS_CAP, ERRORS_CAP,
 * LATENCY_CAP and COST_CAP in backend/app/insights/health.py.
 */
export const COMPONENT_CAPS: Record<ComponentName, number> = {
  findings: 40,
  errors: 30,
  latency: 15,
  cost: 15,
};

/**
 * The worker runs every detector over every project every 15 minutes (`run_detectors`,
 * backend/app/insights/jobs.py): runs that close together belong to one pass.
 */
export const DETECTOR_PASS_WINDOW_MS = 15 * 60_000;

function toNumber(value: string | null): number | null {
  if (value === null || value.trim() === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * A component's observed or baseline value in its own unit: the error rate is a fraction shown as
 * a percent, latency is milliseconds, cost is dollars, findings a count. `null` when unknown.
 */
export function formatComponentValue(name: string, value: string | null): string | null {
  const parsed = toNumber(value);
  if (parsed === null) {
    return null;
  }
  switch (name) {
    case "errors":
      return formatPercent(parsed);
    case "latency":
      return formatDuration(parsed);
    case "cost":
      return formatCost(value);
    case "findings":
      return formatInteger(parsed);
    default:
      return value;
  }
}

/** The share of the ring to fill, 0 to 1. */
export function ringFraction(value: number | null): number {
  return value === null ? 0 : Math.max(0, Math.min(100, value)) / 100;
}

export interface ComponentRow {
  name: ComponentName;
  label: string;
  /** The observed value ("0.0%", "p95 1.29 s", "2 open"); null renders "—". */
  value: string | null;
  /** After the value: "0 of 99 calls", "prev 1.26 s", "no LLM calls in this window". */
  note: string | null;
  /** Points off the score; null when there is nothing to score ("—"). */
  penalty: number | null;
  cap: number;
}

const NO_LLM_CALLS_NOTE = "no LLM calls in this window";
const NO_PREVIOUS_NOTE = "no previous window";

/**
 * The Doctor card's four component rows, always in this order. Without LLM calls (`value` null)
 * the metric rows have nothing to score, but open findings are still real; before the first
 * detector pass (`hasPass` false) the findings are unknown instead.
 */
export function componentRows(health: Health, metrics: Kpis, hasPass: boolean): ComponentRow[] {
  const names: ComponentName[] = ["findings", "errors", "latency", "cost"];
  return names.map((name) => {
    const component = health.components.find((entry) => entry.name === name);
    const row = { name, label: COMPONENT_LABELS[name], cap: COMPONENT_CAPS[name] };
    if (name === "findings") {
      if (!hasPass) {
        return { ...row, value: "No detector pass yet", note: null, penalty: null };
      }
      return {
        ...row,
        value: `${formatInteger(health.open_critical + health.open_warning) ?? "0"} open`,
        note: findingKinds(health),
        penalty: component?.penalty ?? null,
      };
    }
    if (health.value === null || !component) {
      return { ...row, value: null, note: NO_LLM_CALLS_NOTE, penalty: null };
    }
    const observed = formatComponentValue(name, component.observed);
    const baseline = formatComponentValue(name, component.baseline);
    const previous = baseline === null ? NO_PREVIOUS_NOTE : `prev ${baseline}`;
    switch (name) {
      case "errors": {
        const errors = errorCountFrom(metrics.error_rate, metrics.llm_calls);
        const note =
          errors === null
            ? null
            : `${formatInteger(errors) ?? "0"} of ${formatInteger(metrics.llm_calls) ?? "0"} ${pluralNoun(metrics.llm_calls, "call")}`;
        return { ...row, value: observed, note, penalty: component.penalty };
      }
      case "latency":
        return {
          ...row,
          value: observed === null ? null : `p95 ${observed}`,
          note: previous,
          penalty: component.penalty,
        };
      case "cost":
        return { ...row, value: observed, note: previous, penalty: component.penalty };
    }
  });
}

/** "1 critical, 1 warning", only the kinds that are open; null when nothing is. */
function findingKinds(health: Health): string | null {
  const kinds: string[] = [];
  if (health.open_critical > 0) {
    kinds.push(`${health.open_critical} critical`);
  }
  if (health.open_warning > 0) {
    kinds.push(`${health.open_warning} warning`);
  }
  return kinds.length === 0 ? null : kinds.join(", ");
}

/** "0 of 40" or "−20 of 40" (a true minus sign). */
export function formatPoints(penalty: number, cap: number): string {
  return penalty === 0 ? `0 of ${cap}` : `−${penalty} of ${cap}`;
}

/** The penalty meter turns from warning to danger at half its cap. */
export function meterTone(penalty: number, cap: number): "warning" | "danger" {
  return penalty < cap / 2 ? "warning" : "danger";
}

export type StatusTone = "success" | "warning" | "danger" | "none";

export interface StatusLine {
  tone: StatusTone;
  text: string;
}

/**
 * The last detector pass from the newest runs (newest first): every run within
 * `DETECTOR_PASS_WINDOW_MS` of the newest one, how many detectors ran and how many failed.
 */
export function lastPassSummary(runs: readonly DetectorRun[], now: Date): StatusLine {
  const newest = runs[0];
  if (!newest) {
    return { tone: "none", text: "No detector pass yet · first results within 15 min" };
  }
  const cutoff = Date.parse(newest.ran_at) - DETECTOR_PASS_WINDOW_MS;
  const pass = runs.filter((run) => Date.parse(run.ran_at) >= cutoff);
  const detectors = new Set(pass.map((run) => run.detector)).size;
  const failed = pass.filter((run) => run.error !== null).length;
  const when = formatRelativeTime(newest.ran_at, now) ?? "";
  return {
    tone: failed === 0 ? "success" : "warning",
    text: `Last check ${when} · ${pluralize(detectors, "detector")} ran · ${formatInteger(failed) ?? "0"} failed`,
  };
}

/**
 * The providers' status for the providers the project called: their open incidents, or "no open
 * incidents". The incidents page already holds only API-affected incidents (the API's default).
 */
export function providerStatusLine(
  incidents: IncidentList,
  providers: ReadonlySet<string>,
): StatusLine {
  if (incidents.polling === "disabled") {
    return { tone: "none", text: "Provider status feeds off" };
  }
  if (providers.size === 0) {
    return { tone: "none", text: "No provider calls in this window" };
  }
  const open = new Map<string, number>();
  for (const incident of incidents.items) {
    const provider = incident.provider.toLowerCase();
    if (incident.resolved_at === null && providers.has(provider)) {
      open.set(provider, (open.get(provider) ?? 0) + 1);
    }
  }
  if (open.size === 0) {
    const names = [...providers].map(feedLabel).join(", ");
    return { tone: "success", text: `${names} · no open incidents` };
  }
  const text = [...open]
    .map(([provider, count]) => `${feedLabel(provider)} · ${pluralize(count, "open incident")}`)
    .join("; ");
  return { tone: "danger", text };
}
