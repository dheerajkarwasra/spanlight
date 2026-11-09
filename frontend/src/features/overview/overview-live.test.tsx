import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { queryKeys } from "@/lib/api";
import { LiveProvider } from "@/lib/live/live-provider";
import { isStreamLive, useLiveProject } from "@/lib/live/use-live-project";

const PROJECT = "p1";
const WINDOW = { from: "2026-11-04T10:00:00Z", to: "2026-11-04T11:00:00Z" };

/** The browser's `EventSource`, driven by the test. */
class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, (() => void)[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: () => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }
  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.();
  }
  emit(type: string) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener();
    }
  }
}

function latest(): FakeEventSource {
  const source = FakeEventSource.instances.at(-1);
  if (!source) {
    throw new Error("no stream opened");
  }
  return source;
}

function Probe({
  fetchOverview,
  fetchUsers,
  enabled = true,
}: {
  fetchOverview: () => Promise<number>;
  fetchUsers: () => Promise<number>;
  enabled?: boolean;
}) {
  const { status } = useLiveProject(PROJECT, enabled);
  useQuery({ queryKey: queryKeys.project(PROJECT).overview(WINDOW), queryFn: fetchOverview });
  useQuery({ queryKey: [...queryKeys.project(PROJECT).all, "users", WINDOW], queryFn: fetchUsers });
  return <p>{status}</p>;
}

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
  document.dispatchEvent(new Event("visibilitychange"));
}

async function renderLive(usersMs = 0, enabled = true) {
  const fetchOverview = vi.fn(() => Promise.resolve(1));
  const fetchUsers = vi.fn(
    () =>
      new Promise<number>((resolve) => {
        setTimeout(() => {
          resolve(1);
        }, usersMs);
      }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LiveProvider>
        <Probe fetchOverview={fetchOverview} fetchUsers={fetchUsers} enabled={enabled} />
      </LiveProvider>
    </QueryClientProvider>,
  );
  await act(() => vi.advanceTimersByTimeAsync(0));
  return fetchOverview;
}

async function step(ms: number, action?: () => void) {
  await act(async () => {
    action?.();
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("live overview", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("opens one stream for the project and refetches once when it opens", async () => {
    const fetchOverview = await renderLive();
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(latest().url).toBe("/api/v1/projects/p1/live");
    expect(fetchOverview).toHaveBeenCalledTimes(1);

    await step(0, () => {
      latest().open();
    });
    expect(screen.getByText("live")).toBeInTheDocument();
    expect(fetchOverview).toHaveBeenCalledTimes(2);
  });

  it("refetches the overview once on a changed event, and once for three events inside 3 s", async () => {
    const fetchOverview = await renderLive();
    await step(3000, () => {
      latest().open();
    });
    expect(fetchOverview).toHaveBeenCalledTimes(2);

    await step(0, () => {
      latest().emit("changed");
    });
    expect(fetchOverview).toHaveBeenCalledTimes(3);

    // The next two arrive inside the 3 s limit: no fetch until it passes, then one for both.
    await step(2900, () => {
      latest().emit("changed");
      latest().emit("changed");
    });
    expect(fetchOverview).toHaveBeenCalledTimes(3);
    await step(100);
    expect(fetchOverview).toHaveBeenCalledTimes(4);
  });

  it("refetches at once on a change right after the stream opens", async () => {
    const fetchOverview = await renderLive();
    await step(0, () => {
      latest().open();
    });
    expect(fetchOverview).toHaveBeenCalledTimes(2);

    await step(0, () => {
      latest().emit("changed");
    });
    expect(fetchOverview).toHaveBeenCalledTimes(3);
  });

  it("throttles by the slowest refreshed query, not the overview alone", async () => {
    const fetchOverview = await renderLive(1000);
    await step(3000, () => {
      latest().open();
    });
    await step(0, () => {
      latest().emit("changed");
    });
    expect(fetchOverview).toHaveBeenCalledTimes(3);

    // The users query took 1 s: the next refresh waits 10 s, not the overview's 3 s floor.
    await step(5000, () => {
      latest().emit("changed");
    });
    expect(fetchOverview).toHaveBeenCalledTimes(3);
    await step(5000);
    expect(fetchOverview).toHaveBeenCalledTimes(4);
  });

  it("leaves the clock's refresh to the page while it polls", async () => {
    await renderLive();
    await step(0, () => {
      latest().readyState = FakeEventSource.CLOSED;
      latest().onerror?.();
    });
    expect(screen.getByText("polling")).toBeInTheDocument();
    expect(isStreamLive()).toBe(true);
  });

  it("closes the stream while hidden and reopens with one refetch when visible", async () => {
    const fetchOverview = await renderLive();
    await step(3000, () => {
      latest().open();
    });
    const first = latest();

    await step(0, () => {
      setVisibility("hidden");
    });
    expect(first.closed).toBe(true);
    expect(screen.getByText("paused")).toBeInTheDocument();
    const before = fetchOverview.mock.calls.length;

    await step(0, () => {
      setVisibility("visible");
    });
    expect(FakeEventSource.instances).toHaveLength(2);
    await step(0, () => {
      latest().open();
    });
    expect(screen.getByText("live")).toBeInTheDocument();
    expect(fetchOverview).toHaveBeenCalledTimes(before + 1);
  });

  it("falls back to polling when the server refuses the stream, and asks again later", async () => {
    await renderLive();
    await step(0, () => {
      latest().readyState = FakeEventSource.CLOSED;
      latest().onerror?.();
    });
    expect(screen.getByText("polling")).toBeInTheDocument();

    await step(30_000);
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it("stays live on heartbeats, and closes a stalled stream after two missed ones", async () => {
    await renderLive();
    await step(0, () => {
      latest().open();
    });
    for (let i = 0; i < 3; i += 1) {
      await step(20_000, () => {
        latest().emit("heartbeat");
      });
    }
    expect(screen.getByText("live")).toBeInTheDocument();
    const stalled = latest();

    await step(40_000);
    expect(screen.getByText("polling")).toBeInTheDocument();
    expect(stalled.closed).toBe(true);
    await step(30_000);
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it("opens no stream for a fixed window, whose data cannot change", async () => {
    await renderLive(0, false);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("polls without reconnecting when the server hands the stream to a newer tab", async () => {
    await renderLive();
    await step(0, () => {
      latest().open();
    });
    const evicted = latest();
    await step(0, () => {
      evicted.emit("evicted");
    });
    expect(evicted.closed).toBe(true);
    expect(screen.getByText("polling")).toBeInTheDocument();

    await step(120_000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("keeps the server's Retry-After when the tab turns visible while polling", async () => {
    await renderLive();
    await step(0, () => {
      latest().readyState = FakeEventSource.CLOSED;
      latest().onerror?.();
    });
    await step(5_000, () => {
      setVisibility("hidden");
    });
    await step(0, () => {
      setVisibility("visible");
    });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(screen.getByText("polling")).toBeInTheDocument();

    await step(25_000);
    expect(FakeEventSource.instances).toHaveLength(2);
  });
});
