/**
 * The live-updates state machine (spec 2026-11-04 §4.7), kept pure so it is tested without a
 * stream. `connecting` is the first attempt (and the reopen after a hidden tab): no indicator yet.
 */
export type LiveStatus = "live" | "reconnecting" | "paused" | "polling";
export type LiveState = LiveStatus | "connecting";

export type LiveEvent =
  /** The stream opened (first time or after a reconnect). */
  | { type: "open" }
  /** The connection dropped; `EventSource` is retrying by itself. */
  | { type: "error" }
  /** The server refused the stream (LIVE_BUSY, not found, forbidden): `EventSource` gave up. */
  | { type: "failed" }
  /** Two heartbeats in a row never came: the stream is stalled and is closed. */
  | { type: "stalled" }
  | { type: "hidden" }
  | { type: "visible" }
  /** Nobody watches the stream any more. */
  | { type: "reset" };

export const INITIAL_LIVE_STATE: LiveState = "connecting";

/** The server sends a heartbeat event this often. */
export const HEARTBEAT_MS = 20_000;
/** Two missed heartbeats: no `heartbeat` or `changed` for this long means a stalled stream. */
export const STALLED_MS = 2 * HEARTBEAT_MS;
/** The fallback refresh while the stream is unavailable. */
export const LIVE_POLL_MS = 15_000;
/** How long to wait before asking again for a stream that failed (the server's Retry-After). */
export const LIVE_RETRY_MS = 30_000;

export function liveReducer(state: LiveState, event: LiveEvent): LiveState {
  switch (event.type) {
    case "reset":
      return INITIAL_LIVE_STATE;
    case "hidden":
      return "paused";
    case "visible":
      return state === "paused" ? INITIAL_LIVE_STATE : state;
  }
  if (state === "paused") {
    return state;
  }
  switch (event.type) {
    case "open":
      return "live";
    case "error":
      return state === "polling" ? state : "reconnecting";
    case "failed":
    case "stalled":
      return "polling";
  }
}

/** The pause between refreshes: 10 times the last overview request, between 3 s and 30 s. */
export function nextRefreshDelay(lastDurationMs: number): number {
  return Math.min(30_000, Math.max(3_000, 10 * lastDurationMs));
}
