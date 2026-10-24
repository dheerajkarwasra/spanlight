import { describe, expect, it, vi } from "vitest";

import { handleStaleChunk, RELOAD_KEY } from "./stale-chunk-reload";

function memoryStorage(): Pick<Storage, "getItem" | "setItem"> {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
}

describe("handleStaleChunk", () => {
  it("reloads once when a code chunk of an older deploy fails to load", () => {
    const storage = memoryStorage();
    const reload = vi.fn();
    const event = { preventDefault: vi.fn() };

    handleStaleChunk(event, { storage, reload, now: () => 1_000 });

    expect(reload).toHaveBeenCalledOnce();
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(storage.getItem(RELOAD_KEY)).toBe("1000");
  });

  it("doesn't reload again within a minute, so a chunk that is really gone can't loop", () => {
    const storage = memoryStorage();
    const reload = vi.fn();
    handleStaleChunk({ preventDefault: vi.fn() }, { storage, reload, now: () => 1_000 });

    const event = { preventDefault: vi.fn() };
    handleStaleChunk(event, { storage, reload, now: () => 30_000 });

    expect(reload).toHaveBeenCalledOnce();
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  it("reloads again after a minute, for the next deploy", () => {
    const storage = memoryStorage();
    const reload = vi.fn();
    handleStaleChunk({ preventDefault: vi.fn() }, { storage, reload, now: () => 1_000 });
    handleStaleChunk({ preventDefault: vi.fn() }, { storage, reload, now: () => 62_000 });

    expect(reload).toHaveBeenCalledTimes(2);
  });
});
