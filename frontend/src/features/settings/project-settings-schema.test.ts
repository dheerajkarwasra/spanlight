import { describe, expect, it } from "vitest";

import {
  changedProjectFields,
  isEmptyUpdate,
  projectNameSchema,
  projectSavedMessage,
  projectSettingsSchema,
  promotedKeysSchema,
  retentionSchema,
} from "./project-settings-schema";

const project = {
  name: "Support bot",
  retention_days: 30,
  capture_payloads: true,
  weekly_digest_enabled: true,
  insight_channel_ids: [] as string[],
  promoted_metadata_keys: [] as string[],
};

describe("changedProjectFields", () => {
  it("returns only the fields that changed", () => {
    expect(changedProjectFields(project, { name: "Support bot", retention_days: 14 })).toEqual({
      retention_days: 14,
    });
  });

  it("trims the name and ignores whitespace-only differences", () => {
    expect(changedProjectFields(project, { name: "  Support bot  " })).toEqual({});
    expect(changedProjectFields(project, { name: " Billing bot " })).toEqual({
      name: "Billing bot",
    });
  });

  it("includes capture_payloads when it is switched off", () => {
    expect(changedProjectFields(project, { capture_payloads: false })).toEqual({
      capture_payloads: false,
    });
  });

  it("treats an unchanged form as an empty update", () => {
    expect(isEmptyUpdate(changedProjectFields(project, { ...project }))).toBe(true);
  });
});

describe("projectNameSchema", () => {
  it("rejects blank and overly long names", () => {
    expect(projectNameSchema.safeParse({ name: "   " }).success).toBe(false);
    expect(projectNameSchema.safeParse({ name: "x".repeat(101) }).success).toBe(false);
    expect(projectNameSchema.safeParse({ name: "x".repeat(100) }).success).toBe(true);
  });
});

describe("retentionSchema", () => {
  it.each([1, 30, 90])("accepts %i days", (days) => {
    expect(retentionSchema.safeParse({ retention_days: days }).success).toBe(true);
  });

  it.each([0, 91, 1.5, Number.NaN])("rejects %s days", (days) => {
    expect(retentionSchema.safeParse({ retention_days: days }).success).toBe(false);
  });
});

describe("projectSettingsSchema", () => {
  it("validates every field of the combined form", () => {
    expect(projectSettingsSchema.safeParse(project).success).toBe(true);
    expect(projectSettingsSchema.safeParse({ ...project, name: " " }).success).toBe(false);
    expect(projectSettingsSchema.safeParse({ ...project, retention_days: 91 }).success).toBe(false);
  });
});

describe("projectSavedMessage", () => {
  it("names the one setting that changed", () => {
    expect(projectSavedMessage({ name: "Billing bot" })).toBe("Project name saved.");
    expect(projectSavedMessage({ retention_days: 1 })).toBe("Traces are now kept for 1 day.");
    expect(projectSavedMessage({ retention_days: 14 })).toBe("Traces are now kept for 14 days.");
    expect(projectSavedMessage({ capture_payloads: false })).toBe(
      "Prompts and completions will no longer be captured.",
    );
    expect(projectSavedMessage({ capture_payloads: true })).toBe(
      "Prompts and completions will be captured.",
    );
  });

  it("stays general when several settings changed", () => {
    expect(projectSavedMessage({ name: "Bot", retention_days: 7 })).toBe("Project settings saved.");
  });
});

describe("promoted metadata keys", () => {
  const promoted = { ...project, promoted_metadata_keys: ["customer_id", "feature"] };

  it("sends the list only when it changed, order included", () => {
    expect(
      changedProjectFields(promoted, { promoted_metadata_keys: ["customer_id", "feature"] }),
    ).toEqual({});
    expect(
      changedProjectFields(promoted, { promoted_metadata_keys: ["feature", "customer_id"] }),
    ).toEqual({
      promoted_metadata_keys: ["feature", "customer_id"],
    });
    expect(changedProjectFields(promoted, { promoted_metadata_keys: [] })).toEqual({
      promoted_metadata_keys: [],
    });
  });

  it("validates the keys and the cap", () => {
    expect(promotedKeysSchema.safeParse({ promoted_metadata_keys: ["plan"] }).success).toBe(true);
    expect(promotedKeysSchema.safeParse({ promoted_metadata_keys: ["Plan"] }).success).toBe(false);
    expect(
      promotedKeysSchema.safeParse({ promoted_metadata_keys: ["a", "b", "c", "d", "e", "f"] })
        .success,
    ).toBe(false);
  });

  it("has its own saved message", () => {
    expect(projectSavedMessage({ promoted_metadata_keys: ["plan"] })).toBe(
      "Promoted metadata keys saved.",
    );
  });
});
