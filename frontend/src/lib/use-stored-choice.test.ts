import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useChartView, useStoredChoice, useStoredValue } from "./use-stored-choice";

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("useStoredChoice", () => {
  it("starts from the fallback, then remembers the choice", () => {
    const { result } = renderHook(() => useStoredChoice("k", ["on", "off"], "on"));
    expect(result.current[0]).toBe("on");
    act(() => {
      result.current[1]("off");
    });
    expect(result.current[0]).toBe("off");
    expect(window.localStorage.getItem("k")).toBe("off");
  });

  it("reads a stored choice and ignores one it does not know", () => {
    window.localStorage.setItem("k", "off");
    expect(renderHook(() => useStoredChoice("k", ["on", "off"], "on")).result.current[0]).toBe(
      "off",
    );
    window.localStorage.setItem("k", "maybe");
    expect(renderHook(() => useStoredChoice("k", ["on", "off"], "on")).result.current[0]).toBe(
      "on",
    );
  });

  it("still works when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { result } = renderHook(() => useChartView("v"));
    expect(result.current[0]).toBe("chart");
    act(() => {
      result.current[1]("table");
    });
    expect(result.current[0]).toBe("table");
  });
});

describe("useStoredValue", () => {
  it("keeps two readers of one key in step", () => {
    const first = renderHook(() => useStoredValue("shared", "a"));
    const second = renderHook(() => useStoredValue("shared", "a"));
    act(() => {
      first.result.current[1]("b");
    });
    expect(second.result.current[0]).toBe("b");
  });
});
