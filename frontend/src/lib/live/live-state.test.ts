import { describe, expect, it } from "vitest";

import { INITIAL_LIVE_STATE, liveReducer, nextRefreshDelay, type LiveEvent } from "./live-state";

function run(...events: LiveEvent[]) {
  return events.reduce(liveReducer, INITIAL_LIVE_STATE);
}

describe("nextRefreshDelay", () => {
  it("waits 10 times the last overview request, between 3 s and 30 s", () => {
    expect(nextRefreshDelay(50)).toBe(3000);
    expect(nextRefreshDelay(800)).toBe(8000);
    expect(nextRefreshDelay(9000)).toBe(30000);
  });
});

describe("liveReducer", () => {
  it("is live once the stream opens", () => {
    expect(run({ type: "open" })).toBe("live");
  });

  it("reconnects on a dropped connection", () => {
    expect(run({ type: "open" }, { type: "error" })).toBe("reconnecting");
  });

  it("polls after two missed heartbeats, open or reconnecting", () => {
    expect(run({ type: "open" }, { type: "stalled" })).toBe("polling");
    expect(run({ type: "open" }, { type: "error" }, { type: "stalled" })).toBe("polling");
    expect(run({ type: "stalled" })).toBe("polling");
  });

  it("polls when the server refuses the stream (LIVE_BUSY, or the feature is off)", () => {
    expect(run({ type: "failed" })).toBe("polling");
    expect(run({ type: "failed" }, { type: "error" })).toBe("polling");
  });

  it("goes live again when the stream returns", () => {
    expect(run({ type: "failed" }, { type: "open" })).toBe("live");
    expect(run({ type: "open" }, { type: "stalled" }, { type: "open" })).toBe("live");
  });

  it("pauses while the tab is hidden and reconnects when it is visible", () => {
    expect(run({ type: "open" }, { type: "hidden" })).toBe("paused");
    expect(run({ type: "open" }, { type: "hidden" }, { type: "error" })).toBe("paused");
    expect(run({ type: "open" }, { type: "hidden" }, { type: "stalled" })).toBe("paused");
    expect(run({ type: "open" }, { type: "hidden" }, { type: "visible" })).toBe("connecting");
  });
});
