import { describe, expect, it } from "vitest";

import type { RouteConfig, RouteVersion } from "@/lib/api";

import { diffConfigs, versionSummary } from "./version-diff";

const NAMES: Record<string, string> = {
  "cred-aaaa-1111": "Anthropic prod",
  "cred-bbbb-2222": "OpenAI",
};
const nameOf = (id: string) => NAMES[id] ?? null;

function config(overrides: Partial<RouteConfig>): RouteConfig {
  return {
    targets: [{ credential_id: "cred-aaaa-1111", weight: 1, model_aliases: {} }],
    retry: {},
    fallback: {},
    ...overrides,
  };
}

function version(number: number, routeConfig: RouteConfig): RouteVersion {
  return {
    version: number,
    config: routeConfig,
    created_at: "2026-10-01T00:00:00Z",
    updated_by: null,
  };
}

describe("diffConfigs", () => {
  it("treats an omitted policy and the server's explicit defaults as the same", () => {
    const explicit = config({
      retry: {
        max_attempts: 2,
        backoff_ms: 200,
        max_backoff_ms: 2000,
        honour_retry_after: true,
        on_statuses: [408, 409, 429, 500, 502, 503, 504],
      },
      fallback: { on: ["status_5xx", "rate_limited", "timeout", "connection_error"] },
      timeout_ms: 60_000,
    });
    expect(diffConfigs(config({}), explicit, nameOf)).toEqual([]);
  });

  it("shows a target that exists on one side only, field by field, against null", () => {
    const after = config({
      targets: [
        { credential_id: "cred-aaaa-1111", weight: 1, model_aliases: {} },
        { credential_id: "cred-bbbb-2222", weight: 3, model_aliases: { fast: "gpt-4.1-mini" } },
      ],
    });
    expect(diffConfigs(config({}), after, nameOf)).toEqual([
      expect.objectContaining({ path: "targets.1.credential_id", before: null, after: "OpenAI" }),
      expect.objectContaining({ path: "targets.1.weight", before: null, after: "3" }),
      expect.objectContaining({
        path: "targets.1.model_aliases",
        before: null,
        after: "fast → gpt-4.1-mini",
      }),
    ]);
  });

  it("names a deleted credential by its id prefix instead of hiding it", () => {
    const after = config({
      targets: [{ credential_id: "deadbeef-0000", weight: 1, model_aliases: {} }],
    });
    expect(diffConfigs(config({}), after, nameOf)[0]).toMatchObject({
      before: "Anthropic prod",
      after: "Unknown credential deadbeef",
    });
  });

  it("lists changes in config order: targets by index, then retry, fallback, timeout", () => {
    const before = config({
      targets: Array.from({ length: 11 }, () => ({
        credential_id: "cred-aaaa-1111",
        weight: 1,
        model_aliases: {},
      })),
    });
    const after = config({
      targets: before.targets.map((target, index) => ({
        ...target,
        weight: index === 2 || index === 10 ? 5 : 1,
      })),
      timeout_ms: 30_000,
      retry: { max_attempts: 3 },
    });
    expect(diffConfigs(before, after, nameOf).map((change) => change.path)).toEqual([
      "targets.2.weight",
      "targets.10.weight",
      "retry.max_attempts",
      "timeout_ms",
    ]);
  });
});

describe("versionSummary", () => {
  const v1 = version(1, config({}));
  const v2 = version(2, config({ timeout_ms: 30_000 }));
  const v3 = version(3, config({ timeout_ms: 30_000 }));

  it("describes each version against the one before it", () => {
    const versions = [v3, v2, v1];
    expect(versionSummary(v1, versions, nameOf)).toBe("Created the route");
    expect(versionSummary(v2, versions, nameOf)).toMatch(/^Changed /);
    expect(versionSummary(v3, versions, nameOf)).toBe("Same settings as version 2");
  });

  it("falls back to the version number when the previous version is not loaded", () => {
    expect(versionSummary(v3, [v3], nameOf)).toBe("Version 3");
  });
});
