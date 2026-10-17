import { formatInteger } from "@/lib/format";

import { shortDay } from "./window-picker";

/**
 * Formatting for the reconciliation report. Money stays a decimal string from the API to the
 * screen: it is rounded and summed on its digits (as integers), never through a float, so a
 * figure on an audit page is the server's figure. A negative amount reads with a true minus sign.
 */

const MINUS = "−";
const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

interface Scaled {
  /** The value times 10^scale. */
  units: bigint;
  scale: number;
}

function parseDecimal(value: string): Scaled | null {
  const match = DECIMAL.exec(value.trim());
  if (match === null) {
    return null;
  }
  const [, sign = "", whole = "0", fraction = ""] = match;
  const units = BigInt(`${whole}${fraction}`);
  return { units: sign === "-" ? -units : units, scale: fraction.length };
}

function abs(value: bigint): bigint {
  return value < 0n ? -value : value;
}

/** The value in units of 10^-places, rounded half away from zero. */
function roundTo(value: Scaled, places: number): bigint {
  if (value.scale <= places) {
    return value.units * 10n ** BigInt(places - value.scale);
  }
  const divisor = 10n ** BigInt(value.scale - places);
  const size = abs(value.units);
  const quotient = size / divisor;
  const rounded = (size % divisor) * 2n >= divisor ? quotient + 1n : quotient;
  return value.units < 0n ? -rounded : rounded;
}

/** The magnitude of `units` (scaled by 10^places) with thousands separators: "1,234.50". */
function digits(units: bigint, places: number): string {
  const text = abs(units)
    .toString()
    .padStart(places + 1, "0");
  const whole = text.slice(0, text.length - places).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return places === 0 ? whole : `${whole}.${text.slice(text.length - places)}`;
}

/** Whether a decimal string is zero; an unreadable one counts as not zero. */
export function isZeroMoney(value: string): boolean {
  return parseDecimal(value)?.units === 0n;
}

/**
 * "$12.35", "−$5.00"; an amount under a cent keeps four decimals ("$0.0034"), and one under
 * $0.0001 reads "<$0.0001", never "$0.0000".
 */
export function formatUsd(value: string): string {
  const parsed = parseDecimal(value);
  if (parsed === null) {
    return value;
  }
  const negative = parsed.units < 0n;
  const cents = roundTo(parsed, 2);
  const places = cents === 0n && parsed.units !== 0n ? 4 : 2;
  const units = places === 2 ? cents : roundTo(parsed, 4);
  if (units === 0n && parsed.units !== 0n) {
    // Smaller than the last decimal shown: say so rather than print a zero.
    return negative ? `>${MINUS}$0.0001` : "<$0.0001";
  }
  return `${negative ? MINUS : ""}$${digits(units, places)}`;
}

/** A residual or a difference: "+$0.42", "−$1.69", and "$0.00" for zero. */
export function formatSignedUsd(value: string): string {
  const parsed = parseDecimal(value);
  if (parsed === null || parsed.units <= 0n) {
    return formatUsd(value);
  }
  return `+${formatUsd(value)}`;
}

/** The exact sum of decimal strings, trailing zeros dropped: ["0.1", "0.2"] → "0.3". */
export function sumMoney(values: readonly string[]): string {
  const parsed = values.map(parseDecimal).filter((value) => value !== null);
  const scale = Math.max(0, ...parsed.map((value) => value.scale));
  const total = parsed.reduce((sum, value) => sum + roundTo(value, scale), 0n);
  const text = `${total < 0n ? "-" : ""}${digits(total, scale).replaceAll(",", "")}`;
  return scale === 0 ? text : text.replace(/\.?0+$/, "");
}

/** A variance ratio as a signed percentage with one decimal: "0.0310" → "+3.1 %". */
export function formatVariance(ratio: string): string {
  const parsed = parseDecimal(ratio);
  if (parsed === null) {
    return ratio;
  }
  // Percent = ratio × 100: the same digits with the decimal point two places to the right.
  const percent =
    parsed.scale >= 2
      ? { units: parsed.units, scale: parsed.scale - 2 }
      : { units: parsed.units * 10n ** BigInt(2 - parsed.scale), scale: 0 };
  const tenths = roundTo(percent, 1);
  const sign = tenths > 0n ? "+" : tenths < 0n ? MINUS : "";
  return `${sign}${digits(tenths, 1)} %`;
}

/** A token difference: "+19,000", "−6,000", and "0". */
export function formatTokenDifference(difference: number): string {
  const size = formatInteger(Math.abs(difference)) ?? String(Math.abs(difference));
  if (difference === 0) {
    return "0";
  }
  return `${difference > 0 ? "+" : MINUS}${size}`;
}

/** "Oct 7" for a UTC day. */
export function dayLabel(day: string): string {
  return shortDay(day);
}

/** Consecutive days as "Oct 12 – Oct 14" (or "Oct 12"), joined with commas. */
export function formatDayRanges(days: readonly string[]): string | null {
  const sorted = [...days].sort();
  const runs: string[][] = [];
  for (const day of sorted) {
    const run = runs.at(-1);
    const previous = run?.at(-1);
    const next = previous ? new Date(Date.parse(`${previous}T00:00:00Z`) + 86_400_000) : null;
    if (run && next?.toISOString().slice(0, 10) === day) {
      run.push(day);
    } else {
      runs.push([day]);
    }
  }
  const labels = runs.map((run) => {
    const [first = "", last = first] = [run[0], run.at(-1)];
    return first === last ? dayLabel(first) : `${dayLabel(first)} – ${dayLabel(last)}`;
  });
  return labels.length > 0 ? labels.join(", ") : null;
}
