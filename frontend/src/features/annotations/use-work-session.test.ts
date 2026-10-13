import { act, renderHook, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { QueueItem } from "@/lib/api";

import { useWorkSession } from "./use-work-session";

const params = vi.hoisted(() => ({ projectId: "proj_1" }));

const api = vi.hoisted(() => ({
  claim: vi.fn<(projectId: string, queueId: string) => Promise<QueueItem | undefined>>(),
  release: vi.fn<(projectId: string, queueId: string, itemId: string) => Promise<void>>(),
  complete: vi.fn(),
  skip: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  annotationsApi: api,
}));
vi.mock("@/features/shell", () => ({ useProjectParams: () => params }));
vi.mock("@/features/scores", () => ({ useRefreshScores: () => () => Promise.resolve() }));
vi.mock("./annotation-queries", () => ({ useInvalidateQueues: () => () => Promise.resolve() }));

function makeItem(id: string): QueueItem {
  return {
    id,
    trace_id: `trace_${id}`,
    span_id: null,
    status: "pending",
    assigned_to: "u1",
    assigned_at: "2026-10-13T00:00:00Z",
    completed_by: null,
    completed_at: null,
    created_at: "2026-10-13T00:00:00Z",
  };
}

/**
 * A queue with one pending item per queue id, claimed the way the API does it: the claim holds the
 * row until its transaction commits, and a second claim that arrives meanwhile skips the locked row
 * (`FOR UPDATE SKIP LOCKED`) and finds nothing.
 */
function serveOneItemPerQueue() {
  const settle: (() => void)[] = [];
  const inFlight = new Set<string>();
  api.claim.mockImplementation((_projectId, queueId) => {
    if (inFlight.has(queueId)) {
      return Promise.resolve(undefined);
    }
    inFlight.add(queueId);
    return new Promise((resolve) => {
      settle.push(() => {
        inFlight.delete(queueId);
        resolve(makeItem(`item_${queueId}`));
      });
    });
  });
  return {
    /** Lets every claim that is waiting on the server answer. */
    answerClaims: async () => {
      await act(async () => {
        settle.splice(0).forEach((answer) => answer());
        await Promise.resolve();
      });
    },
  };
}

beforeEach(() => {
  api.release.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useWorkSession claim on entry", () => {
  it("claims once when the page remounts on the same queue before the claim answers", async () => {
    // The app shell swaps its layout once navigation resolves, so the page mounts, unmounts and
    // mounts again on the same queue while the first claim is still on the wire.
    const server = serveOneItemPerQueue();
    const first = renderHook(() => useWorkSession("queue_a"));
    first.unmount();
    const second = renderHook(() => useWorkSession("queue_a"));

    await server.answerClaims();

    await waitFor(() => expect(second.result.current.state.phase).not.toBe("claiming"));
    expect(api.claim).toHaveBeenCalledTimes(1);
    expect(api.release).not.toHaveBeenCalled();
    const state = second.result.current.state;
    expect(state.phase === "working" ? state.item.id : null).toBe("item_queue_a");
  });

  it("claims once under StrictMode's mount, unmount, mount", async () => {
    const server = serveOneItemPerQueue();
    const { result } = renderHook(() => useWorkSession("queue_a"), { wrapper: StrictMode });

    await server.answerClaims();

    await waitFor(() => expect(result.current.state.phase).not.toBe("claiming"));
    expect(result.current.state.phase).toBe("working");
    expect(api.claim).toHaveBeenCalledTimes(1);
    expect(api.release).not.toHaveBeenCalled();
  });

  it("gives the item back when the page closes before the claim answers", async () => {
    const server = serveOneItemPerQueue();
    const { unmount } = renderHook(() => useWorkSession("queue_a"));
    unmount();

    await server.answerClaims();

    await waitFor(() =>
      expect(api.release).toHaveBeenCalledWith("proj_1", "queue_a", "item_queue_a"),
    );
    expect(api.release).toHaveBeenCalledTimes(1);
  });

  it("gives the item back once when the page that adopted the claim closes too", async () => {
    const server = serveOneItemPerQueue();
    renderHook(() => useWorkSession("queue_a")).unmount();
    renderHook(() => useWorkSession("queue_a")).unmount();

    await server.answerClaims();

    await waitFor(() => expect(api.release).toHaveBeenCalled());
    expect(api.claim).toHaveBeenCalledTimes(1);
    expect(api.release).toHaveBeenCalledTimes(1);
  });

  it("gives the first queue's item back when the page moves to another queue mid-claim", async () => {
    const server = serveOneItemPerQueue();
    const { result, rerender } = renderHook(({ queueId }) => useWorkSession(queueId), {
      initialProps: { queueId: "queue_a" },
    });
    rerender({ queueId: "queue_b" });

    await server.answerClaims();

    await waitFor(() => expect(result.current.state.phase).toBe("working"));
    const state = result.current.state;
    expect(state.phase === "working" ? state.item.id : null).toBe("item_queue_b");
    expect(api.release).toHaveBeenCalledTimes(1);
    expect(api.release).toHaveBeenCalledWith("proj_1", "queue_a", "item_queue_a");
  });

  it("releases the held item when the page closes", async () => {
    const server = serveOneItemPerQueue();
    const { result, unmount } = renderHook(() => useWorkSession("queue_a"));
    await server.answerClaims();
    await waitFor(() => expect(result.current.state.phase).toBe("working"));

    unmount();

    expect(api.release).toHaveBeenCalledWith("proj_1", "queue_a", "item_queue_a");
  });
});
