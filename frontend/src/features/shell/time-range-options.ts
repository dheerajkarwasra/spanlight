import { RANGE_PRESETS, type RangePreset } from "@/lib/time-range";

/**
 * How a page uses the header's range and environment controls. Every project page shows both; a
 * control the page can't honour is drawn disabled with the reason, and the URL keeps the shared
 * value for the other pages.
 */
export interface TimeRangeOptions {
  /** The presets the page offers; the picker draws every preset and disables the rest. */
  presets: readonly RangePreset[];
  /** Whether the calendar pill for a custom from/to range is enabled. */
  allowCustom: boolean;
  /**
   * The preset the page reads when the shared range is one it doesn't offer (30 d or custom from
   * another page). The picker shows it as selected; the URL keeps the shared range for the other
   * pages.
   */
  fallback?: RangePreset;
  /** The whole range control is disabled; the reason. */
  rangeDisabled?: string;
  /** The environment pill is disabled; the reason. */
  environmentDisabled?: string;
  /** Why the presets the page doesn't offer are disabled. */
  presetNote?: string;
}

export const DEFAULT_TIME_RANGE_OPTIONS: TimeRangeOptions = {
  presets: RANGE_PRESETS,
  allowCustom: true,
};

/**
 * The gateway overview endpoint accepts at most 7 days, so no 30 d and no custom range; a shared
 * range it doesn't offer reads as the last 7 days.
 */
export const GATEWAY_TIME_RANGE_OPTIONS = {
  presets: ["1h", "24h", "7d"],
  allowCustom: false,
  fallback: "7d",
  presetNote: "The gateway overview covers at most 7 days.",
} as const satisfies TimeRangeOptions;
