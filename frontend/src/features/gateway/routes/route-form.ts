import { z } from "zod";

import type { FallbackCondition, RouteConfig } from "@/lib/api";

/*
 * The route editor's form: a zod mirror of the backend `RouteConfig` (gateway/route_config.py),
 * with the same bounds, so a config the form accepts is one the server accepts. Model aliases are
 * edited as rows and sent as a map.
 */

export const MAX_TARGETS = 8;
export const WEIGHT_MIN = 1;
export const WEIGHT_MAX = 100;
export const MAX_MODEL_ALIASES = 32;
export const MAX_ATTEMPTS_MIN = 1;
export const MAX_ATTEMPTS_MAX = 5;
export const STATUS_MIN = 400;
export const STATUS_MAX = 599;
export const MAX_RETRY_STATUSES = 16;
export const BACKOFF_MAX_MS = 10_000;
export const MAX_BACKOFF_MAX_MS = 30_000;
export const TIMEOUT_MIN_MS = 1_000;
export const TIMEOUT_MAX_MS = 600_000;
export const ROUTE_NAME_MAX_LENGTH = 100;

/** The backend's `ModelName`: what a client requests or a provider knows, e.g. `claude-sonnet-4-5`. */
export const MODEL_NAME_PATTERN = /^[A-Za-z0-9._:/-]{1,128}$/;

/**
 * The server's default `fallback.on` (`DEFAULT_FALLBACK_CONDITIONS` in `route_config.py`): every
 * condition but `auth_error`, which a route has to list itself.
 */
export const DEFAULT_FALLBACK_CONDITIONS: readonly FallbackCondition[] = [
  "status_5xx",
  "rate_limited",
  "timeout",
  "connection_error",
];

/** Every condition, in the canonical order the form shows and sends them. */
export const FALLBACK_CONDITIONS = [
  ...DEFAULT_FALLBACK_CONDITIONS,
  "auth_error",
] as const satisfies readonly FallbackCondition[];

/**
 * `selected` with `condition` ticked or cleared, in canonical order. Conditions the person did not
 * touch stay as they were, so an opt-in made through the API survives a save from the form.
 */
export function toggledConditions(
  selected: readonly FallbackCondition[],
  condition: FallbackCondition,
  checked: boolean,
): FallbackCondition[] {
  const next = new Set(selected);
  if (checked) {
    next.add(condition);
  } else {
    next.delete(condition);
  }
  return FALLBACK_CONDITIONS.filter((candidate) => next.has(candidate));
}

export const DEFAULT_RETRY_STATUSES: readonly number[] = [408, 409, 429, 500, 502, 503, 504];

function wholeNumber(min: number, max: number, message: string) {
  return z.number({ error: message }).int(message).min(min, message).max(max, message);
}

const modelName = z
  .string()
  .trim()
  .regex(MODEL_NAME_PATTERN, "Use 1–128 letters, digits or . _ : / -");

const aliasRowSchema = z.object({ from: modelName, to: modelName });

const targetSchema = z.object({
  credential_id: z.string().min(1, "Pick a credential."),
  weight: wholeNumber(WEIGHT_MIN, WEIGHT_MAX, "Weight must be a whole number from 1 to 100."),
  aliases: z
    .array(aliasRowSchema)
    .max(MAX_MODEL_ALIASES, `A target can have up to ${MAX_MODEL_ALIASES} aliases.`)
    .superRefine((rows, context) => {
      const seen = new Set<string>();
      rows.forEach((row, index) => {
        const from = row.from.trim();
        if (seen.has(from)) {
          context.addIssue({
            code: "custom",
            path: [index, "from"],
            message: "This model already has an alias.",
          });
        }
        seen.add(from);
      });
    }),
});

const retrySchema = z
  .object({
    max_attempts: wholeNumber(
      MAX_ATTEMPTS_MIN,
      MAX_ATTEMPTS_MAX,
      "Max attempts must be a whole number from 1 to 5.",
    ),
    backoff_ms: wholeNumber(0, BACKOFF_MAX_MS, "Backoff must be a whole number from 0 to 10,000."),
    max_backoff_ms: wholeNumber(
      0,
      MAX_BACKOFF_MAX_MS,
      "Max backoff must be a whole number up to 30,000.",
    ),
    honour_retry_after: z.boolean(),
    on_statuses: z
      .array(z.number().int().min(STATUS_MIN).max(STATUS_MAX))
      .max(MAX_RETRY_STATUSES, `Up to ${MAX_RETRY_STATUSES} statuses.`)
      .refine((statuses) => new Set(statuses).size === statuses.length, "Each status once."),
  })
  .superRefine((retry, context) => {
    if (
      Number.isInteger(retry.backoff_ms) &&
      Number.isInteger(retry.max_backoff_ms) &&
      retry.max_backoff_ms < retry.backoff_ms
    ) {
      context.addIssue({
        code: "custom",
        path: ["max_backoff_ms"],
        message: `Must be at least the backoff (${retry.backoff_ms} ms).`,
      });
    }
  });

export const routeFormSchema = z
  .object({
    targets: z
      .array(targetSchema)
      .min(1, "A route needs at least one target.")
      .max(MAX_TARGETS, `A route can have up to ${MAX_TARGETS} targets.`),
    retry: retrySchema,
    fallback: z.object({
      on: z
        .array(z.enum(FALLBACK_CONDITIONS))
        .refine((conditions) => new Set(conditions).size === conditions.length),
    }),
    timeout_ms: wholeNumber(
      TIMEOUT_MIN_MS,
      TIMEOUT_MAX_MS,
      "Timeout must be a whole number from 1,000 to 600,000.",
    ),
  })
  .superRefine((values, context) => {
    // The server's `auth_error_fallback` rule: falling back to the key that was just refused
    // would only be refused again.
    const credentials = new Set(values.targets.map((target) => target.credential_id));
    if (values.fallback.on.includes("auth_error") && credentials.size < 2) {
      context.addIssue({
        code: "custom",
        path: ["fallback", "on"],
        message: "Auth errors need two targets on different credentials.",
      });
    }
  });

export type RouteFormValues = z.infer<typeof routeFormSchema>;
export type AliasRow = RouteFormValues["targets"][number]["aliases"][number];

/** The New route dialog's one field. The backend trims it and allows 1–100 characters. */
export const routeNameSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Enter a route name.")
    .max(ROUTE_NAME_MAX_LENGTH, `Use ${ROUTE_NAME_MAX_LENGTH} characters or fewer.`),
});

export function aliasRowsFromMap(aliases: Record<string, string> | undefined): AliasRow[] {
  return Object.entries(aliases ?? {}).map(([from, to]) => ({ from, to }));
}

export function aliasMapFromRows(rows: readonly AliasRow[]): Record<string, string> {
  return Object.fromEntries(rows.map((row) => [row.from.trim(), row.to.trim()]));
}

/** A stored config as form values, with the server's defaults for anything left out. */
export function formValuesFromConfig(config: RouteConfig): RouteFormValues {
  return {
    targets: config.targets.map((target) => ({
      credential_id: target.credential_id,
      weight: target.weight ?? 1,
      aliases: aliasRowsFromMap(target.model_aliases),
    })),
    retry: {
      max_attempts: config.retry.max_attempts ?? 2,
      backoff_ms: config.retry.backoff_ms ?? 200,
      max_backoff_ms: config.retry.max_backoff_ms ?? 2000,
      honour_retry_after: config.retry.honour_retry_after ?? true,
      on_statuses: [...(config.retry.on_statuses ?? DEFAULT_RETRY_STATUSES)],
    },
    fallback: { on: [...(config.fallback.on ?? DEFAULT_FALLBACK_CONDITIONS)] },
    timeout_ms: config.timeout_ms ?? 60_000,
  };
}

export function configFromFormValues(values: RouteFormValues): RouteConfig {
  return {
    targets: values.targets.map((target) => ({
      credential_id: target.credential_id,
      weight: target.weight,
      model_aliases: aliasMapFromRows(target.aliases),
    })),
    retry: {
      max_attempts: values.retry.max_attempts,
      backoff_ms: values.retry.backoff_ms,
      max_backoff_ms: values.retry.max_backoff_ms,
      honour_retry_after: values.retry.honour_retry_after,
      on_statuses: [...values.retry.on_statuses],
    },
    fallback: { on: [...values.fallback.on] },
    timeout_ms: values.timeout_ms,
  };
}

/** A new route: one target on the first credential, and the server's default policies. */
export function newRouteFormValues(credentialId: string | null): RouteFormValues {
  return formValuesFromConfig({
    targets: [{ credential_id: credentialId ?? "", weight: 1, model_aliases: {} }],
    retry: {},
    fallback: {},
  });
}

/**
 * Where a server field error belongs in the form. The API reports paths relative to the request
 * body (`config.targets.0.weight`); alias errors land on the target's alias list, and a status
 * error on the whole status list, since a single chip has no message of its own.
 */
export function formFieldFromServerPath(path: string): string | null {
  const parts = path.split(".");
  if (parts[0] !== "config") {
    return null;
  }
  const rest = parts.slice(1);
  if (rest[0] === "targets") {
    if (rest.length < 3) {
      return "targets";
    }
    const [, index, field] = rest;
    if (field === "model_aliases") {
      return `targets.${index ?? ""}.aliases`;
    }
    return field === "credential_id" || field === "weight"
      ? `targets.${index ?? ""}.${field}`
      : null;
  }
  if (rest[0] === "retry") {
    const field = rest[1];
    if (field === "on_statuses") {
      return "retry.on_statuses";
    }
    return field === "max_attempts" ||
      field === "backoff_ms" ||
      field === "max_backoff_ms" ||
      field === "honour_retry_after"
      ? `retry.${field}`
      : null;
  }
  if (rest[0] === "fallback") {
    return "fallback.on";
  }
  return rest[0] === "timeout_ms" ? "timeout_ms" : null;
}

interface ErrorNode {
  message?: unknown;
  [key: string]: unknown;
}

/**
 * Every field path with an error in a react-hook-form errors tree, in form order, e.g.
 * `["targets.0.weight", "retry.max_backoff_ms"]`. The save bar counts and names them.
 */
export function errorPaths(errors: unknown, prefix = ""): string[] {
  if (typeof errors !== "object" || errors === null) {
    return [];
  }
  const node = errors as ErrorNode;
  if (typeof node.message === "string" && prefix !== "") {
    return [prefix];
  }
  const paths: string[] = [];
  for (const [key, child] of Object.entries(node)) {
    if (key === "ref" || key === "type" || key === "types" || key === "message") {
      continue;
    }
    // A field array's own error (e.g. too many aliases) sits under `root`: it names the array.
    const path = key === "root" ? prefix : prefix === "" ? key : `${prefix}.${key}`;
    paths.push(...errorPaths(child, path));
  }
  return paths;
}
