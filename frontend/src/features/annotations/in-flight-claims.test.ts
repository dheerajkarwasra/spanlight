import { afterEach, describe, expect, it, vi } from "vitest";

import type { QueueItem } from "@/lib/api";

import { claimFor } from "./in-flight-claims";

const api = vi.hoisted(() => ({
  claim: vi.fn<(projectId: string, queueId: string) => Promise<QueueItem | undefined>>(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  annotationsApi: api,
}));

/** A claim the test answers (or fails) when it chooses. */
function deferredClaim() {
  let resolve: (item: QueueItem | undefined) => void = () => undefined;
  let reject: (error: unknown) => void = () => undefined;
  const answer = new Promise<QueueItem | undefined>((onAnswer, onFail) => {
    resolve = onAnswer;
    reject = onFail;
  });
  return { answer, resolve, reject };
}

/** Lets the claim's settle handlers run. */
async function settled(answer: Promise<unknown>): Promise<void> {
  await answer.catch(() => undefined);
  await Promise.resolve();
}

let queueCounter = 0;
/** A queue no other test has used, since the claims are module-level state. */
function freshKey() {
  queueCounter += 1;
  return { projectId: "proj_1", queueId: `queue_${queueCounter}` };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("claimFor", () => {
  it("adopts an orphaned claim on the same queue instead of sending a second one", () => {
    const key = freshKey();
    api.claim.mockReturnValueOnce(deferredClaim().answer);

    const first = claimFor(key);
    first.orphaned = true;
    const adopted = claimFor(key);

    expect(adopted).toBe(first);
    expect(adopted.orphaned).toBe(false);
    expect(api.claim).toHaveBeenCalledTimes(1);
  });

  it("sends a new claim when the pending one still belongs to a live session", () => {
    const key = freshKey();
    api.claim
      .mockReturnValueOnce(deferredClaim().answer)
      .mockReturnValueOnce(deferredClaim().answer);

    const first = claimFor(key);
    const second = claimFor(key);

    expect(second).not.toBe(first);
    expect(api.claim).toHaveBeenCalledTimes(2);
  });

  it("forgets a claim once it answers, so an orphan is never adopted after the fact", async () => {
    const key = freshKey();
    const wire = deferredClaim();
    api.claim.mockReturnValueOnce(wire.answer).mockReturnValueOnce(deferredClaim().answer);

    const first = claimFor(key);
    wire.resolve(undefined);
    await settled(first.answer);
    first.orphaned = true;
    const next = claimFor(key);

    expect(next).not.toBe(first);
    expect(api.claim).toHaveBeenCalledTimes(2);
  });

  it("forgets a claim that failed", async () => {
    const key = freshKey();
    const wire = deferredClaim();
    api.claim.mockReturnValueOnce(wire.answer).mockReturnValueOnce(deferredClaim().answer);

    const first = claimFor(key);
    wire.reject(new Error("network"));
    await settled(first.answer);
    first.orphaned = true;

    expect(claimFor(key)).not.toBe(first);
  });

  it("keeps claims on different queues apart", () => {
    const one = freshKey();
    const other = freshKey();
    api.claim
      .mockReturnValueOnce(deferredClaim().answer)
      .mockReturnValueOnce(deferredClaim().answer);

    const first = claimFor(one);
    first.orphaned = true;

    expect(claimFor(other)).not.toBe(first);
    expect(api.claim).toHaveBeenLastCalledWith(other.projectId, other.queueId);
  });
});
