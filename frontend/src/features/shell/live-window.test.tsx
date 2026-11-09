import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setStreamLive } from "@/lib/live/use-live-project";
import { resolveRange } from "@/lib/time-range";

import { LIVE_TICK_MS, useLiveNow } from "./live-window";

// Mid-minute, so one tick later the window has not moved.
const START = new Date("2026-11-04T10:00:05Z");

function Page({ fetchWindow }: { fetchWindow: () => Promise<number> }) {
  const now = useLiveNow();
  const { to } = resolveRange({ range: "1h" }, now);
  useQuery({ queryKey: ["page", { to }], queryFn: fetchWindow });
  return null;
}

async function renderPage() {
  const fetchWindow = vi.fn(() => Promise.resolve(1));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Page fetchWindow={fetchWindow} />
    </QueryClientProvider>,
  );
  await act(() => vi.advanceTimersByTimeAsync(0));
  return fetchWindow;
}

describe("live window refresh", () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: START });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  });
  afterEach(() => {
    setStreamLive(false);
    vi.useRealTimers();
  });

  it("refreshes a page without a live stream every tick", async () => {
    const fetchWindow = await renderPage();
    const loaded = fetchWindow.mock.calls.length;

    await act(() => vi.advanceTimersByTimeAsync(LIVE_TICK_MS));
    expect(fetchWindow).toHaveBeenCalledTimes(loaded + 1);
  });

  it("leaves the refresh to the stream while it is live", async () => {
    setStreamLive(true);
    const fetchWindow = await renderPage();
    const loaded = fetchWindow.mock.calls.length;

    await act(() => vi.advanceTimersByTimeAsync(LIVE_TICK_MS));
    expect(fetchWindow).toHaveBeenCalledTimes(loaded);
  });
});
