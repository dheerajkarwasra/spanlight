import { describe, expect, it } from "vitest";

import { runBulk } from "./run-bulk";

describe("runBulk", () => {
  it("returns every id as ok when each action succeeds", async () => {
    const result = await runBulk(["a", "b", "c"], (id) => Promise.resolve(id));
    expect(result).toEqual({ ok: ["a", "b", "c"], failed: [] });
  });

  it("keeps going past a failure and reports it with its error", async () => {
    const boom = new Error("409");
    const result = await runBulk(["a", "b", "c"], (id) =>
      id === "b" ? Promise.reject(boom) : Promise.resolve(id),
    );
    expect(result.ok).toEqual(["a", "c"]);
    expect(result.failed).toEqual([{ id: "b", error: boom }]);
  });

  it("never runs more than four actions at once", async () => {
    let running = 0;
    let peak = 0;
    const ids = Array.from({ length: 12 }, (_, index) => `id${String(index)}`);
    const result = await runBulk(ids, async () => {
      running += 1;
      peak = Math.max(peak, running);
      await new Promise((resolve) => setTimeout(resolve, 1));
      running -= 1;
    });
    expect(peak).toBe(4);
    expect(result.ok).toHaveLength(12);
  });
});
