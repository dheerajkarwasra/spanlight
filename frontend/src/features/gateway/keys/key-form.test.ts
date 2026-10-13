import { describe, expect, it } from "vitest";

import type { GatewayKey } from "@/lib/api";

import {
  CACHE_OPTIONS,
  EMPTY_KEY_FORM,
  cacheLabel,
  cacheOptionsFor,
  cacheTtlFromValue,
  faultProductionConflict,
  faultSelectDisabled,
  formValuesFromKey,
  keyFormSchema,
  modelNameProblem,
  parseLimit,
  parseList,
  tagProblem,
  toCreateInput,
  toUpdateInput,
  type KeyFormValues,
} from "./key-form";

// Bounds from `app/gateway/key_schemas.py`: limits are strict integers (rpm 1..100,000, tpm
// 1..100,000,000), the cache TTL is 1..86,400 s or off, model names follow `ModelName`, and an
// edit that sends null clears a limit.

function form(overrides: Partial<KeyFormValues>): KeyFormValues {
  return { ...EMPTY_KEY_FORM, name: "Support bot", environment: "staging", ...overrides };
}

function issuePaths(values: KeyFormValues): string[] {
  const result = keyFormSchema.safeParse(values);
  return result.success ? [] : result.error.issues.map((issue) => issue.path.join("."));
}

function key(overrides: Partial<GatewayKey>): GatewayKey {
  return {
    id: "k1",
    name: "Support bot",
    prefix: "spl_gw_abcd",
    environment: "staging",
    route_id: null,
    rpm_limit: null,
    tpm_limit: null,
    cache_ttl_seconds: null,
    allowed_models: [],
    default_tags: [],
    fault_profile_id: null,
    created_by: null,
    created_at: "2026-10-01T00:00:00Z",
    last_used_at: null,
    revoked_at: null,
    ...overrides,
  };
}

describe("limits", () => {
  it("accept thousands separators and leave an empty field as no limit (null, not 0)", () => {
    expect(parseLimit("1,000")).toBe(1000);
    expect(parseLimit(" ")).toBeNull();
    expect(issuePaths(form({ rpmLimit: "100,000", tpmLimit: "" }))).toEqual([]);
  });

  it("refuse 0, decimals, negatives and values over the server's maximum", () => {
    for (const rpmLimit of ["0", "1.5", "-5", "100001", "1e3"]) {
      expect(issuePaths(form({ rpmLimit }))).toEqual(["rpmLimit"]);
    }
    expect(issuePaths(form({ tpmLimit: "100000001" }))).toEqual(["tpmLimit"]);
  });
});

describe("names and lists", () => {
  it("trim and bound the name and environment", () => {
    expect(issuePaths(form({ name: "   " }))).toEqual(["name"]);
    expect(issuePaths(form({ environment: "e".repeat(65) }))).toEqual(["environment"]);
  });

  it("parse a comma list trimmed, without empties or repeats (the server refuses repeats)", () => {
    expect(parseList(" gpt-4.1, ,claude-sonnet-4-5,gpt-4.1 ")).toEqual([
      "gpt-4.1",
      "claude-sonnet-4-5",
    ]);
  });

  it("allow only model names the server's pattern accepts", () => {
    expect(modelNameProblem("anthropic/claude-sonnet-4-5:beta", [])).toBeNull();
    expect(modelNameProblem("gpt 4", [])).not.toBeNull();
    expect(modelNameProblem("m".repeat(129), [])).not.toBeNull();
    expect(issuePaths(form({ allowedModels: ["ok-model", "bad model"] }))).toEqual([
      "allowedModels.1",
    ]);
  });

  it("cap tags at 20 of 64 characters", () => {
    expect(tagProblem("t".repeat(65), [])).toMatch(/64 characters/);
    const twenty = Array.from({ length: 20 }, (_, index) => `t${String(index)}`);
    expect(tagProblem("one-more", twenty)).toMatch(/at most 20/);
  });
});

describe("cache TTL", () => {
  it("maps the presets and off", () => {
    expect(cacheTtlFromValue("off")).toBeNull();
    expect(cacheTtlFromValue("3600")).toBe(3600);
    expect(cacheLabel(null)).toBe("Off");
    expect(cacheLabel(86400)).toBe("24 h");
  });

  it("keeps a TTL set through the API that is not a preset, so an edit never drops it", () => {
    expect(cacheOptionsFor(90)).toEqual([...CACHE_OPTIONS, { value: "90", label: "90 s" }]);
    const stored = key({ cache_ttl_seconds: 90 });
    expect(toUpdateInput(formValuesFromKey(stored), stored)).toEqual({});
  });
});

describe("request bodies", () => {
  it("send no route for the project default on create", () => {
    expect(toCreateInput(form({ rpmLimit: "60" }))).toEqual({
      name: "Support bot",
      environment: "staging",
      route_id: null,
      rpm_limit: 60,
      tpm_limit: null,
      cache_ttl_seconds: null,
      allowed_models: [],
      default_tags: [],
    });
  });

  it("send nothing for an untouched edit", () => {
    const stored = key({ rpm_limit: 1000, allowed_models: ["gpt-4.1"], route_id: "r1" });
    expect(toUpdateInput(formValuesFromKey(stored), stored)).toEqual({});
  });

  it("clear a limit and the fault profile with an explicit null", () => {
    const stored = key({ rpm_limit: 1000, fault_profile_id: "fp1" });
    const values = { ...formValuesFromKey(stored), rpmLimit: "", faultProfileId: "" };
    expect(toUpdateInput(values, stored)).toEqual({ rpm_limit: null, fault_profile_id: null });
  });
});

describe("fault profiles on production keys", () => {
  it("lock the selector for any spelling of production, as the server does", () => {
    expect(faultSelectDisabled({ environment: " Production ", faultProfileId: "" })).toBe(true);
    expect(faultSelectDisabled({ environment: "production-eu", faultProfileId: "" })).toBe(false);
  });

  it("flag a profile still attached after switching to production", () => {
    expect(
      faultProductionConflict(form({ environment: "PRODUCTION", faultProfileId: "fp1" })),
    ).toBe(true);
    expect(faultProductionConflict(form({ environment: "staging", faultProfileId: "fp1" }))).toBe(
      false,
    );
  });
});
