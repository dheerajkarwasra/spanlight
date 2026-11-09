import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useState,
  type ActionDispatch,
  type ReactNode,
} from "react";

import { queryKeys } from "@/lib/api";
import { projectPath } from "@/lib/api/paths";

import {
  INITIAL_LIVE_STATE,
  LIVE_RETRY_MS,
  liveReducer,
  nextRefreshDelay,
  STALLED_MS,
  type LiveEvent,
} from "./live-state";
import { LiveContext, setStreamLive } from "./use-live-project";

/**
 * One live-updates stream per tab (spec 2026-11-04 §4.7), shared by every page of the project
 * that watches it, and open only while one does.
 */
export function LiveProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [watchers, setWatchers] = useState<string[]>([]);
  const [status, dispatch] = useReducer(liveReducer, INITIAL_LIVE_STATE);
  const projectId = watchers[0] ?? null;

  const watch = useCallback((id: string) => {
    setWatchers((current) => [...current, id]);
    return () => {
      setWatchers((current) => {
        const index = current.indexOf(id);
        return index === -1 ? current : current.filter((_, i) => i !== index);
      });
    };
  }, []);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    return connect(client, projectId, dispatch);
  }, [client, projectId]);

  // The watching page refreshes itself while live or polling; other pages keep the clock's refresh.
  useEffect(() => {
    setStreamLive(status === "live" || status === "polling");
    return () => {
      setStreamLive(false);
    };
  }, [status]);

  const value = useMemo(() => ({ status, watch }), [status, watch]);
  return <LiveContext value={value}>{children}</LiveContext>;
}

/** Opens the stream and refreshes the project's live queries on each change. Returns the cleanup. */
function connect(
  client: QueryClient,
  projectId: string,
  dispatch: ActionDispatch<[LiveEvent]>,
): () => void {
  const keys = queryKeys.project(projectId);
  const refreshed = [
    [...keys.all, "timeseries"],
    [...keys.all, "models"],
    [...keys.doctor, "health"],
    [...keys.all, "users"],
    [...keys.all, "latest-traces"],
  ];
  let source: EventSource | null = null;
  let retryTimer: number | undefined;
  let stallTimer: number | undefined;
  let trailingTimer: number | undefined;
  let lastDurationMs = 0;
  let blockedUntil = 0;
  // When a refused stream may be asked for again (the server's Retry-After).
  let retryAt = 0;

  function invalidate(reuse: boolean) {
    const options = { cancelRefetch: !reuse };
    return Promise.all(
      [[...keys.all, "overview"], ...refreshed].map((queryKey) =>
        client.invalidateQueries({ queryKey }, options),
      ),
    );
  }

  // On every open: join a refetch already in flight (the page's own load, or the focus refetch
  // on return to the tab) instead of cancelling it, and leave the throttle alone.
  function refreshOnOpen() {
    void invalidate(true);
  }

  function refresh() {
    const now = Date.now();
    if (now < blockedUntil) {
      trailingTimer ??= window.setTimeout(() => {
        trailingTimer = undefined;
        refresh();
      }, blockedUntil - now);
      return;
    }
    blockedUntil = now + nextRefreshDelay(lastDurationMs);
    void invalidate(false).then(() => {
      // A heavy page refreshes less often by itself: timed over every query it refetched.
      lastDurationMs = Date.now() - now;
      blockedUntil = Math.max(blockedUntil, now + nextRefreshDelay(lastDurationMs));
    });
  }

  // Poll, and ask for a stream again after the server's Retry-After.
  function fallBack(event: LiveEvent) {
    close();
    dispatch(event);
    retryAt = Date.now() + LIVE_RETRY_MS;
    retryTimer = window.setTimeout(open, LIVE_RETRY_MS);
  }

  // Two missed heartbeats (no `heartbeat` or `changed` for `STALLED_MS`), open or reconnecting:
  // the stream is stalled, e.g. behind a buffering proxy.
  function armStallTimer() {
    window.clearTimeout(stallTimer);
    stallTimer = window.setTimeout(() => {
      fallBack({ type: "stalled" });
    }, STALLED_MS);
  }

  function open() {
    if (typeof EventSource === "undefined") {
      dispatch({ type: "failed" });
      return;
    }
    const stream = new EventSource(`${projectPath(projectId)}/live`);
    source = stream;
    armStallTimer();
    stream.onopen = () => {
      armStallTimer();
      dispatch({ type: "open" });
      // A change between the page's load and the server's baseline sends no event: refetch once.
      refreshOnOpen();
    };
    stream.addEventListener("heartbeat", armStallTimer);
    stream.addEventListener("changed", () => {
      armStallTimer();
      refresh();
    });
    // The session opened more streams than the server keeps and this one was the oldest: poll
    // until the tab is shown again, since reconnecting would evict the next tab, and so on.
    stream.addEventListener("evicted", () => {
      close();
      dispatch({ type: "failed" });
    });
    stream.onerror = () => {
      if (stream.readyState !== EventSource.CLOSED) {
        dispatch({ type: "error" });
        return;
      }
      // Refused (LIVE_BUSY, or live updates are off).
      fallBack({ type: "failed" });
    };
  }

  function close() {
    source?.close();
    source = null;
    window.clearTimeout(retryTimer);
    window.clearTimeout(stallTimer);
    window.clearTimeout(trailingTimer);
    trailingTimer = undefined;
  }

  function onVisibility() {
    if (document.visibilityState === "hidden") {
      close();
      dispatch({ type: "hidden" });
    } else if (source === null) {
      dispatch({ type: "visible" });
      const wait = retryAt - Date.now();
      if (wait > 0) {
        // Still inside a refusal's Retry-After: keep polling until it passes.
        dispatch({ type: "failed" });
        retryTimer = window.setTimeout(open, wait);
      } else {
        open();
      }
    }
  }

  document.addEventListener("visibilitychange", onVisibility);
  if (document.visibilityState === "hidden") {
    dispatch({ type: "hidden" });
  } else {
    open();
  }

  return () => {
    close();
    document.removeEventListener("visibilitychange", onVisibility);
    dispatch({ type: "reset" });
  };
}
