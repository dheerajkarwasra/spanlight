import { useState } from "react";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    // Storage can be blocked (private windows, site data off): the caller's fallback applies.
    return null;
  }
}

function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Not remembered this time; the control still works.
  }
}

/**
 * A per-viewer, per-browser choice kept in `localStorage` under `key`: one of `choices`, or
 * `fallback` when nothing (or something unrecognised) is stored or storage is blocked.
 */
export function useStoredChoice<T extends string>(
  key: string,
  choices: readonly T[],
  fallback: T,
): [T, (next: T) => void] {
  const [value, setValue] = useState<T>(() => {
    const stored = read(key);
    return choices.find((choice) => choice === stored) ?? fallback;
  });
  function change(next: T) {
    setValue(next);
    write(key, next);
  }
  return [value, change];
}

/** Whether a chart card shows its chart or its table. */
export type ChartView = "chart" | "table";

const CHART_VIEWS: readonly ChartView[] = ["chart", "table"];

/** A chart card's view, remembered under `key`; the chart by default. */
export function useChartView(key: string): [ChartView, (next: ChartView) => void] {
  return useStoredChoice(key, CHART_VIEWS, "chart");
}
