import { createContext, use, useEffect } from "react";

import { LIVE_POLL_MS, type LiveState } from "./live-state";

interface LiveContextValue {
  status: LiveState;
  /** Keeps the project's stream open until the returned function is called. */
  watch: (projectId: string) => () => void;
}

/** Provided by `LiveProvider`; without it a page neither streams nor polls. */
export const LiveContext = createContext<LiveContextValue | null>(null);

/**
 * Keeps the project's live stream open while the calling page is mounted (spec 2026-11-04 §4.7).
 * `changed` events refresh the overview, timeseries, models, health, users and latest-traces
 * queries; while the stream is unavailable, `refetchInterval` polls them instead. `enabled` is
 * false for a custom window: it is fixed in the past, so it neither streams nor polls.
 */
export function useLiveProject(
  projectId: string,
  enabled = true,
): {
  status: LiveState;
  refetchInterval: number | false;
} {
  const live = use(LiveContext);
  const watch = enabled ? live?.watch : undefined;
  useEffect(() => watch?.(projectId), [watch, projectId]);
  const status = enabled ? (live?.status ?? "connecting") : "connecting";
  return { status, refetchInterval: status === "polling" ? LIVE_POLL_MS : false };
}

// Whether this tab's stream is live: the clock's 30 s refresh (`live-window.ts`) then leaves the
// watching page to the stream, and refreshes every other page as before.
let streamLive = false;

export function setStreamLive(live: boolean): void {
  streamLive = live;
}

export function isStreamLive(): boolean {
  return streamLive;
}
