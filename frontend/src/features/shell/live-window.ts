import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useCallback, useSyncExternalStore } from "react";

import { RANGE_PRESETS, resolveRange } from "@/lib/time-range";

/** How often an open, visible page re-reads its preset window ("Last hour" and so on). */
export const LIVE_TICK_MS = 30_000;

/** The minute every preset window ends at, for the moment `now`. */
function windowEnd(now: Date): string {
  return resolveRange({ range: "1h" }, now).to;
}

/** Whether a query's key holds a window that ends at `end`. */
function endsAt(queryKey: readonly unknown[], end: string): boolean {
  return JSON.stringify(queryKey).includes(`"${end}"`);
}

/**
 * Copies every cached query whose window ends at `prev`'s minute to the same key with the window
 * moved to `next`'s minute, marked stale. The page then keeps showing its figures (no skeleton,
 * no placeholder dimming) while the moved window loads, and an infinite list keeps its loaded
 * pages, refetching each one.
 */
export function carryForward(client: QueryClient, prev: Date, next: Date): void {
  const moves = RANGE_PRESETS.map((preset) => [
    resolveRange({ range: preset }, prev).from,
    resolveRange({ range: preset }, next).from,
  ]);
  const oldEnd = windowEnd(prev);
  moves.push([oldEnd, windowEnd(next)]);
  for (const query of client.getQueryCache().getAll()) {
    if (query.state.data === undefined || !endsAt(query.queryKey, oldEnd)) {
      continue;
    }
    let key = JSON.stringify(query.queryKey);
    for (const [from, to] of moves) {
      key = key.replaceAll(`"${from}"`, `"${to}"`);
    }
    const movedKey = JSON.parse(key) as unknown[];
    if (client.getQueryData(movedKey) === undefined) {
      client.setQueryData(movedKey, query.state.data, { updatedAt: 0 });
    }
  }
}

// One clock for the whole app, so every query on a page moves to the same window together.
let clockNow = new Date();
const listeners = new Set<() => void>();
let stopClock: (() => void) | null = null;

function tick(client: QueryClient): void {
  if (document.visibilityState !== "visible") {
    return;
  }
  const next = new Date();
  const end = windowEnd(clockNow);
  if (windowEnd(next) === end) {
    // Same minute: the window is unchanged, so refresh what the page shows for it.
    void client.invalidateQueries({ predicate: (query) => endsAt(query.queryKey, end) });
    return;
  }
  carryForward(client, clockNow, next);
  clockNow = next;
  for (const listener of listeners) {
    listener();
  }
}

function startClock(client: QueryClient): () => void {
  const onTick = () => {
    tick(client);
  };
  const timer = window.setInterval(onTick, LIVE_TICK_MS);
  document.addEventListener("visibilitychange", onTick);
  onTick();
  return () => {
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", onTick);
  };
}

/**
 * "Now" for preset windows: moves once a minute while a page that reads it is open and visible,
 * and in between refreshes the queries for the current window every `LIVE_TICK_MS`. Hidden tabs
 * don't poll.
 */
export function useLiveNow(): Date {
  const client = useQueryClient();
  const subscribe = useCallback(
    (listener: () => void) => {
      listeners.add(listener);
      stopClock ??= startClock(client);
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) {
          stopClock?.();
          stopClock = null;
        }
      };
    },
    [client],
  );
  return useSyncExternalStore(subscribe, () => clockNow);
}
