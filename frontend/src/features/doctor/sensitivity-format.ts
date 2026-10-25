import type { Sensitivity, SensitivityLevel } from "@/lib/api";
import { formatInteger } from "@/lib/format";

/** The three settings, least strict first (spec §4.4). */
export const LEVELS: readonly SensitivityLevel[] = ["high_recall", "balanced", "high_precision"];

const LABELS: Record<SensitivityLevel, string> = {
  high_recall: "High recall",
  balanced: "Balanced",
  high_precision: "High precision",
};

const DESCRIPTIONS: Record<SensitivityLevel, string> = {
  high_recall: "Catch every pattern; dismiss the wrong ones.",
  balanced: "Fewer findings; small spikes are skipped.",
  high_precision: "Only strong signals; may miss early problems.",
};

export function levelLabel(level: SensitivityLevel): string {
  return LABELS[level];
}

export function levelDescription(level: SensitivityLevel): string {
  return DESCRIPTIONS[level];
}

/** Whether moving from `from` to `to` can only drop findings. */
export function isStricter(from: SensitivityLevel, to: SensitivityLevel): boolean {
  return LEVELS.indexOf(to) > LEVELS.indexOf(from);
}

const BASELINE = " vs baseline";

/** The gate as a value and as a phrase; null at High recall and for a fixed check. */
function gateParts(item: Sensitivity, level: SensitivityLevel) {
  if (level === "high_recall" || item.gates === null || item.metric_label === null) {
    return null;
  }
  const label = item.metric_label;
  const gate = Number(item.gates[level]);
  // Shares and rates are stored as fractions (0.25); ratios are against the detector's baseline.
  if (/\b(rate|share)\b/.test(label)) {
    const value = `${formatInteger(gate * 100) ?? ""}\u00a0%`;
    return { value, phrase: `${label} ${value}` };
  }
  if (label.endsWith(BASELINE)) {
    const value = `${String(gate)}× baseline`;
    return { value, phrase: `${label.slice(0, -BASELINE.length)} ${value}` };
  }
  const count = formatInteger(gate) ?? String(gate);
  return { value: `${count} ${label}`, phrase: `${label} ${count}` };
}

/** The gate in plain words, e.g. "error rate 25 % or more". */
export function gateText(item: Sensitivity, level: SensitivityLevel): string {
  if (!item.tunable) {
    return "One setting";
  }
  const parts = gateParts(item, level);
  return parts === null ? "Today’s rule (default)" : `${parts.phrase} or more`;
}

/** What a finding must reach at `level`, for "findings below …", e.g. "10 %". */
export function gateValue(item: Sensitivity, level: SensitivityLevel): string {
  return gateParts(item, level)?.value ?? "";
}
