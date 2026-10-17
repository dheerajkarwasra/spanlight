import { describe, expect, it } from "vitest";

import { addKey, keyProblem, MAX_PROMOTED_KEYS, METADATA_KEY_PATTERN } from "./promoted-keys";

describe("keyProblem", () => {
  it.each(["customer_id", "workflow.run-kind", "a", "x".repeat(64)])("accepts %s", (key) => {
    expect(keyProblem(key)).toBeNull();
  });

  it.each(["Customer", "Customer ID", "plan tier", "", "x".repeat(65), "émoji"])(
    "explains why %s is not a key",
    (key) => {
      expect(keyProblem(key)).toBe(
        "Use lower-case letters, digits, dots, dashes or underscores, up to 64 characters.",
      );
    },
  );

  it("matches the server's key pattern", () => {
    expect(METADATA_KEY_PATTERN.source).toBe("^[a-z0-9_.-]{1,64}$");
  });
});

describe("addKey", () => {
  it("appends a new key and keeps the order", () => {
    expect(addKey(["customer_id"], "feature")).toEqual(["customer_id", "feature"]);
  });

  it("does not add a key twice", () => {
    expect(addKey(["customer_id", "feature"], "feature")).toEqual(["customer_id", "feature"]);
  });

  it("stops at the cap", () => {
    const full = ["a", "b", "c", "d", "e"];
    expect(full).toHaveLength(MAX_PROMOTED_KEYS);
    expect(addKey(full, "f")).toEqual(full);
  });
});
