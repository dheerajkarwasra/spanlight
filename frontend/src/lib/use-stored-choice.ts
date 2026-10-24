import { useEffect, useState } from "react";

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

/** Tells the other readers of a key in this tab (`storage` events only reach other tabs). */
const CHANGED = "spanlight:stored-value";

/**
 * A per-viewer, per-browser string kept in `localStorage` under `key`, or `fallback` when nothing
 * is stored or storage is blocked. A blocked write still changes the value for this session.
 * Every reader of the same key on the page sees a change at once.
 */
export function useStoredValue(key: string, fallback: string): [string, (next: string) => void] {
  const [value, setValue] = useState(() => read(key) ?? fallback);
  useEffect(() => {
    const sync = (event: Event) => {
      const detail = (event as CustomEvent<{ key: string; value: string }>).detail;
      if (detail.key === key) setValue(detail.value);
    };
    window.addEventListener(CHANGED, sync);
    return () => {
      window.removeEventListener(CHANGED, sync);
    };
  }, [key]);
  function change(next: string) {
    setValue(next);
    write(key, next);
    window.dispatchEvent(new CustomEvent(CHANGED, { detail: { key, value: next } }));
  }
  return [value, change];
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
  const [stored, change] = useStoredValue(key, fallback);
  return [choices.find((choice) => choice === stored) ?? fallback, change];
}

/** Whether a chart card shows its chart or its table. */
export type ChartView = "chart" | "table";

const CHART_VIEWS: readonly ChartView[] = ["chart", "table"];

/** A chart card's view, remembered under `key`; the chart by default. */
export function useChartView(key: string): [ChartView, (next: ChartView) => void] {
  return useStoredChoice(key, CHART_VIEWS, "chart");
}
