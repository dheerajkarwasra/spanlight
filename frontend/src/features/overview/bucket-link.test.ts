import { describe, expect, it } from "vitest";

import { bucketSearch } from "./bucket-link";

describe("bucketSearch", () => {
  it("opens the traces list on the clicked hour", () => {
    expect(bucketSearch("2026-10-16T14:00:00Z", "hour")).toEqual({
      range: "custom",
      from: "2026-10-16T14:00:00.000Z",
      to: "2026-10-16T15:00:00.000Z",
    });
  });

  it("covers a whole day, keeps the environment and narrows to failed traces", () => {
    expect(bucketSearch("2026-10-16T00:00:00Z", "day", "production", true)).toEqual({
      range: "custom",
      from: "2026-10-16T00:00:00.000Z",
      to: "2026-10-17T00:00:00.000Z",
      env: "production",
      status: "error",
    });
  });
});
