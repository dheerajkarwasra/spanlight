import { describe, expect, it } from "vitest";

import {
  DEFAULT_RETRY_STATUSES,
  FALLBACK_CONDITIONS,
  configFromFormValues,
  errorPaths,
  formFieldFromServerPath,
  formValuesFromConfig,
  newRouteFormValues,
  routeFormSchema,
  routeNameSchema,
  type RouteFormValues,
} from "./route-form";

// Mirrors `RouteConfig` in `app/gateway/route_config.py`: what the form accepts, the server must
// accept, and a new route starts from the server's own defaults.

function valid(): RouteFormValues {
  return newRouteFormValues("cred_1");
}

function firstTarget(values: RouteFormValues): RouteFormValues["targets"][number] {
  const target = values.targets[0];
  if (target === undefined) throw new Error("no target");
  return target;
}

function issuePaths(values: RouteFormValues): string[] {
  const result = routeFormSchema.safeParse(values);
  return result.success ? [] : result.error.issues.map((issue) => issue.path.join("."));
}

describe("newRouteFormValues", () => {
  it("starts from the server's defaults", () => {
    const values = valid();
    expect(values.retry).toEqual({
      max_attempts: 2,
      backoff_ms: 200,
      max_backoff_ms: 2000,
      honour_retry_after: true,
      on_statuses: [...DEFAULT_RETRY_STATUSES],
    });
    expect(values.fallback.on).toEqual([...FALLBACK_CONDITIONS]);
    expect(values.timeout_ms).toBe(60_000);
    expect(issuePaths(values)).toEqual([]);
  });

  it("needs a credential when the project has none yet", () => {
    expect(issuePaths(newRouteFormValues(null))).toEqual(["targets.0.credential_id"]);
  });
});

describe("routeFormSchema", () => {
  it("bounds targets to 1..8 and weights to whole numbers 1..100", () => {
    expect(issuePaths({ ...valid(), targets: [] })).toEqual(["targets"]);
    const target = firstTarget(valid());
    expect(issuePaths({ ...valid(), targets: Array.from({ length: 9 }, () => target) })).toEqual([
      "targets",
    ]);
    expect(issuePaths({ ...valid(), targets: [{ ...target, weight: 0 }] })).toEqual([
      "targets.0.weight",
    ]);
    expect(issuePaths({ ...valid(), targets: [{ ...target, weight: 1.5 }] })).toEqual([
      "targets.0.weight",
    ]);
  });

  it("refuses a max backoff below the backoff", () => {
    const values = valid();
    values.retry = { ...values.retry, backoff_ms: 5000, max_backoff_ms: 2000 };
    expect(issuePaths(values)).toEqual(["retry.max_backoff_ms"]);
  });

  it("refuses repeated statuses and statuses outside 400..599", () => {
    const values = valid();
    values.retry = { ...values.retry, on_statuses: [429, 429] };
    expect(issuePaths(values)).toEqual(["retry.on_statuses"]);
    values.retry = { ...values.retry, on_statuses: [399] };
    expect(issuePaths(values)).toEqual(["retry.on_statuses.0"]);
  });

  it("bounds the timeout to 1,000..600,000 ms", () => {
    expect(issuePaths({ ...valid(), timeout_ms: 999 })).toEqual(["timeout_ms"]);
    expect(issuePaths({ ...valid(), timeout_ms: 600_001 })).toEqual(["timeout_ms"]);
  });

  it("refuses two aliases for the same requested model, even with stray spaces", () => {
    const values = valid();
    firstTarget(values).aliases = [
      { from: "fast", to: "claude-haiku-4-5" },
      { from: " fast ", to: "gpt-4.1-mini" },
    ];
    expect(issuePaths(values)).toEqual(["targets.0.aliases.1.from"]);
  });

  it("refuses alias names the server's ModelName pattern refuses", () => {
    const values = valid();
    firstTarget(values).aliases = [{ from: "fast model", to: "claude-haiku-4-5" }];
    expect(issuePaths(values)).toEqual(["targets.0.aliases.0.from"]);
  });
});

describe("config round trip", () => {
  it("sends trimmed aliases as a map and reads them back as rows", () => {
    const values = valid();
    firstTarget(values).aliases = [{ from: " fast ", to: " claude-haiku-4-5 " }];
    const config = configFromFormValues(values);
    expect(config.targets[0]?.model_aliases).toEqual({ fast: "claude-haiku-4-5" });
    expect(formValuesFromConfig(config).targets[0]?.aliases).toEqual([
      { from: "fast", to: "claude-haiku-4-5" },
    ]);
  });

  it("keeps an explicitly empty fallback list (never fall back) instead of the default", () => {
    const config = configFromFormValues({ ...valid(), fallback: { on: [] } });
    expect(formValuesFromConfig(config).fallback.on).toEqual([]);
  });
});

describe("routeNameSchema", () => {
  it("trims and bounds the name to 1..100 characters", () => {
    expect(routeNameSchema.safeParse({ name: "  " }).success).toBe(false);
    expect(routeNameSchema.safeParse({ name: "n".repeat(101) }).success).toBe(false);
    expect(routeNameSchema.parse({ name: "  support  " }).name).toBe("support");
  });
});

describe("server errors on the form", () => {
  it("maps request paths onto form fields", () => {
    expect(formFieldFromServerPath("config.targets.2.weight")).toBe("targets.2.weight");
    expect(formFieldFromServerPath("config.targets.0.model_aliases.fast")).toBe(
      "targets.0.aliases",
    );
    expect(formFieldFromServerPath("config.retry.on_statuses.3")).toBe("retry.on_statuses");
    expect(formFieldFromServerPath("config.fallback.on.0")).toBe("fallback.on");
    expect(formFieldFromServerPath("name")).toBeNull();
  });

  it("lists every field with an error, including a field array's own error", () => {
    const errors = {
      targets: { 0: { weight: { message: "bad", type: "custom" } }, root: { message: "too many" } },
      retry: { max_backoff_ms: { message: "low", ref: {} } },
    };
    expect(errorPaths(errors)).toEqual(["targets.0.weight", "targets", "retry.max_backoff_ms"]);
  });
});
