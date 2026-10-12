/**
 * Display formatting. Every formatter returns `null` for unknown input so the
 * caller can render the shared <Unknown /> placeholder instead of a fake zero.
 */

const integerFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const compactFormat = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const percentFormat = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

type Maybe<T> = T | null | undefined;

export function formatInteger(value: Maybe<number>): string | null {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return null;
  }
  return integerFormat.format(value);
}

/** 1234 → "1.2K"; small values are shown exactly. */
export function formatCompact(value: Maybe<number>): string | null {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return null;
  }
  if (Math.abs(value) < 10_000) {
    return integerFormat.format(value);
  }
  return compactFormat.format(value);
}

export function formatPercent(ratio: Maybe<number>): string | null {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) {
    return null;
  }
  return percentFormat.format(ratio);
}

export function formatDuration(ms: Maybe<number>): string | null {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) {
    return null;
  }
  if (ms < 1) {
    return "<1 ms";
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  if (ms < 60_000) {
    const seconds = ms / 1000;
    return `${seconds < 10 ? seconds.toFixed(2) : seconds.toFixed(1)} s`;
  }
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

/** Parses a decimal money string from the API. Returns null for null or garbage. */
export function parseMoney(value: Maybe<string>): number | null {
  if (value === null || value === undefined || value.trim() === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatCost(value: Maybe<string | number>): string | null {
  const amount = typeof value === "number" ? value : parseMoney(value);
  if (amount === null) {
    return null;
  }
  if (amount === 0) {
    return "$0.00";
  }
  const absolute = Math.abs(amount);
  let digits = 2;
  if (absolute < 0.01) {
    digits = 6;
  } else if (absolute < 1) {
    digits = 4;
  }
  return `$${amount.toFixed(digits)}`;
}

const dateTimeFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const dateFormat = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
});

export function formatTimestamp(iso: Maybe<string>): string | null {
  if (!iso) {
    return null;
  }
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : dateTimeFormat.format(date);
}

export function formatDate(iso: Maybe<string>): string | null {
  if (!iso) {
    return null;
  }
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : dateFormat.format(date);
}

const relativeFormat = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

export function formatRelativeTime(iso: Maybe<string>, now: Date = new Date()): string | null {
  if (!iso) {
    return null;
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  const deltaSeconds = (date.getTime() - now.getTime()) / 1000;
  for (const [unit, secondsInUnit] of RELATIVE_UNITS) {
    if (Math.abs(deltaSeconds) >= secondsInUnit) {
      return relativeFormat.format(Math.round(deltaSeconds / secondsInUnit), unit);
    }
  }
  return "just now";
}

/**
 * Relative change between two periods, as a ratio (0.12 = +12%).
 * Null when either side is unknown or the previous period is zero.
 */
export function relativeChange(current: Maybe<number>, previous: Maybe<number>): number | null {
  if (current === null || current === undefined || previous === null || previous === undefined) {
    return null;
  }
  if (previous === 0) {
    return null;
  }
  return (current - previous) / previous;
}

export function shortId(id: string, length = 8): string {
  return id.length > length ? id.slice(0, length) : id;
}

/** "turn" or "turns": the noun for a count, without the count. */
export function pluralNoun(count: number, singular: string, plural = `${singular}s`): string {
  return count === 1 ? singular : plural;
}

/** "1 turn", "1,200 errors": the count with thousands separators and the matching noun. */
export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${formatInteger(count) ?? String(count)} ${pluralNoun(count, singular, plural)}`;
}

/** "support/answer v3": a prompt version as every screen names it. */
export function formatPromptVersion(name: string, version: number): string {
  return `${name} v${String(version)}`;
}
