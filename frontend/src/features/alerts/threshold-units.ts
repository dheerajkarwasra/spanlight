import type { Metric } from "@/lib/api";

/**
 * How a threshold is typed in the rule editor and how it travels to the API. People think in
 * percent, seconds and dollars; the API stores ratios, milliseconds and dollars as decimal strings.
 * The conversion shifts the decimal point on the string itself, so "5" percent becomes exactly
 * "0.05" and "1.5" seconds exactly "1500", with no floating-point rounding on the way.
 */

export interface ThresholdUnit {
  /** Shown before the number inside the field ("$"). */
  prefix?: string;
  /** Shown after the number inside the field ("%", "s"). */
  suffix?: string;
  /** Powers of ten from the typed number to the API's: -2 for percent → ratio, 3 for s → ms. */
  shift: number;
  /**
   * Decimals a person may type: exactly what the API's 6 decimal places allow after the shift, so
   * any stored threshold loads back into the field unchanged and validates as it is.
   */
  maxDecimals: number;
  /** The unit in words, for the field's accessible name ("in seconds"). */
  spoken: string;
}

export const THRESHOLD_UNITS: Record<Metric, ThresholdUnit> = {
  error_rate: { suffix: "%", shift: -2, maxDecimals: 6 - 2, spoken: "percent" },
  p95_ms: { suffix: "s", shift: 3, maxDecimals: 6 + 3, spoken: "seconds" },
  ttft_p95_ms: { suffix: "s", shift: 3, maxDecimals: 6 + 3, spoken: "seconds" },
  cost_usd: { prefix: "$", shift: 0, maxDecimals: 6, spoken: "US dollars" },
  llm_calls: { suffix: "calls", shift: 0, maxDecimals: 6, spoken: "LLM calls" },
  tokens: { suffix: "tokens", shift: 0, maxDecimals: 6, spoken: "tokens" },
  error_calls: { suffix: "calls", shift: 0, maxDecimals: 6, spoken: "failed LLM calls" },
};

/** The API keeps 21 digits with 6 decimals, so at most 15 before the point. */
const MAX_WHOLE_DIGITS = 15;

const PLAIN_DECIMAL = /^\d+(\.\d+)?$/;

/** "007.500" → "7.5", "0.000" → "0". */
function normalize(whole: string, fraction: string): string {
  const cleanWhole = whole.replace(/^0+(?=\d)/, "") || "0";
  const cleanFraction = fraction.replace(/0+$/, "");
  return cleanFraction === "" ? cleanWhole : `${cleanWhole}.${cleanFraction}`;
}

/** Moves the decimal point of a plain decimal string `places` to the right (left if negative). */
export function shiftDecimal(value: string, places: number): string {
  const [whole = "0", fraction = ""] = value.split(".");
  const digits = whole + fraction;
  const point = whole.length + places;
  if (point <= 0) {
    return normalize("0", "0".repeat(-point) + digits);
  }
  if (point >= digits.length) {
    return normalize(digits + "0".repeat(point - digits.length), "");
  }
  return normalize(digits.slice(0, point), digits.slice(point));
}

/** Why the typed threshold can't be sent, or null when it can. */
export function thresholdError(metric: Metric, typed: string): string | null {
  const text = typed.trim();
  if (!PLAIN_DECIMAL.test(text)) {
    return "Enter a number, 0 or higher.";
  }
  const { maxDecimals, shift } = THRESHOLD_UNITS[metric];
  const decimals = text.split(".")[1]?.length ?? 0;
  if (decimals > maxDecimals) {
    return `Use at most ${maxDecimals} decimal places.`;
  }
  const whole = shiftDecimal(text, shift).split(".")[0] ?? "";
  return whole.length > MAX_WHOLE_DIGITS ? "Enter a smaller number." : null;
}

/** The typed threshold as the API's decimal string. Call only once `thresholdError` is null. */
export function toApiThreshold(metric: Metric, typed: string): string {
  return shiftDecimal(typed.trim(), THRESHOLD_UNITS[metric].shift);
}

/** A stored threshold as the editor shows it; "" when there is none or it is unreadable. */
export function fromApiThreshold(metric: Metric, value: string | null): string {
  if (value === null || !PLAIN_DECIMAL.test(value)) {
    return "";
  }
  return shiftDecimal(value, -THRESHOLD_UNITS[metric].shift);
}
