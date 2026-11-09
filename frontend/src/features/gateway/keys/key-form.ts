import { z } from "zod";

import type { GatewayKey, GatewayKeyCreate, GatewayKeyUpdate } from "@/lib/api";

import { isProductionEnvironment } from "../environment";

/*
 * The pure half of the key forms: the zod schema with the backend's bounds, the comma-list and
 * cache-TTL conversions, and the translation between form values and API payloads.
 */

export const KEY_NAME_MAX_LENGTH = 100;
export const ENVIRONMENT_MAX_LENGTH = 64;
export const RPM_LIMIT_MAX = 100_000;
export const TPM_LIMIT_MAX = 100_000_000;
export const MAX_ALLOWED_MODELS = 100;
export const MAX_DEFAULT_TAGS = 20;
export const TAG_MAX_LENGTH = 64;

/** The backend's `ModelName` pattern: 1 to 128 characters of letters, digits and `._:/-`. */
const MODEL_NAME_PATTERN = /^[A-Za-z0-9._:/-]{1,128}$/;

export const MODEL_NAME_MESSAGE =
  "Use letters, digits and . _ : / - only, up to 128 characters, for example claude-sonnet-4-5.";

/* ---------- Comma lists ---------- */

/** `"a, b,, a"` → `["a", "b"]`: trimmed, empties dropped, repeats removed, order kept. */
export function parseList(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(",")) {
    const item = part.trim();
    if (item !== "") {
      seen.add(item);
    }
  }
  return [...seen];
}

/* ---------- Cache TTL ---------- */

export interface CacheOption {
  /** `"off"` or the TTL in seconds as text. */
  value: string;
  label: string;
}

export const CACHE_OFF = "off";

/** Figma "Cache": Off, 5 min, 1 h, 24 h. */
export const CACHE_OPTIONS: readonly CacheOption[] = [
  { value: CACHE_OFF, label: "Off" },
  { value: "300", label: "5 min" },
  { value: "3600", label: "1 h" },
  { value: "86400", label: "24 h" },
];

/** The form value of a key's TTL: `null` is off. */
export function cacheValueFromTtl(ttlSeconds: number | null): string {
  return ttlSeconds === null ? CACHE_OFF : String(ttlSeconds);
}

/** The TTL a cache option stands for: `null` for off. */
export function cacheTtlFromValue(value: string): number | null {
  if (value === CACHE_OFF) {
    return null;
  }
  const seconds = Number(value);
  return Number.isInteger(seconds) && seconds > 0 ? seconds : null;
}

/** "5 min", "1 h", or the seconds for a TTL set through the API that is not a preset. */
export function cacheLabel(ttlSeconds: number | null): string {
  const value = cacheValueFromTtl(ttlSeconds);
  const preset = CACHE_OPTIONS.find((option) => option.value === value);
  return preset ? preset.label : `${value} s`;
}

/** The presets, plus the key's own TTL when the API was used to set one that is not a preset. */
export function cacheOptionsFor(ttlSeconds: number | null): readonly CacheOption[] {
  const value = cacheValueFromTtl(ttlSeconds);
  if (CACHE_OPTIONS.some((option) => option.value === value)) {
    return CACHE_OPTIONS;
  }
  return [...CACHE_OPTIONS, { value, label: cacheLabel(ttlSeconds) }];
}

/** Why a model name may not join the allowed list, or null. */
export function modelNameProblem(item: string, current: readonly string[]): string | null {
  if (!MODEL_NAME_PATTERN.test(item)) {
    return MODEL_NAME_MESSAGE;
  }
  return current.length >= MAX_ALLOWED_MODELS
    ? `Allow at most ${MAX_ALLOWED_MODELS} models.`
    : null;
}

/** Why a tag may not join the default tags, or null. */
export function tagProblem(item: string, current: readonly string[]): string | null {
  if (item.length > TAG_MAX_LENGTH) {
    return `Use ${TAG_MAX_LENGTH} characters or fewer for a tag.`;
  }
  return current.length >= MAX_DEFAULT_TAGS ? `Use at most ${MAX_DEFAULT_TAGS} tags.` : null;
}

/* ---------- Schema ---------- */

function limitField(max: number) {
  const message = `Enter a whole number from 1 to ${max.toLocaleString("en-US")}, or leave it empty.`;
  return z.string().refine((text) => {
    const digits = text.replace(/[,\s]/g, "");
    if (digits === "") {
      return true;
    }
    return /^\d+$/.test(digits) && Number(digits) >= 1 && Number(digits) <= max;
  }, message);
}

/** The text of a limit field as the API wants it: `null` when empty. */
export function parseLimit(text: string): number | null {
  const digits = text.replace(/[,\s]/g, "");
  return digits === "" ? null : Number(digits);
}

export const keyFormSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Enter a name for this key.")
    .max(KEY_NAME_MAX_LENGTH, `Use ${KEY_NAME_MAX_LENGTH} characters or fewer.`),
  environment: z
    .string()
    .trim()
    .min(1, "Choose or enter an environment.")
    .max(ENVIRONMENT_MAX_LENGTH, `Use ${ENVIRONMENT_MAX_LENGTH} characters or fewer.`),
  /** A route id; empty means "the project's default route". */
  routeId: z.string(),
  rpmLimit: limitField(RPM_LIMIT_MAX),
  tpmLimit: limitField(TPM_LIMIT_MAX),
  allowedModels: z
    .array(z.string().regex(MODEL_NAME_PATTERN, MODEL_NAME_MESSAGE))
    .max(MAX_ALLOWED_MODELS, `Allow at most ${MAX_ALLOWED_MODELS} models.`),
  defaultTags: z
    .array(
      z.string().min(1).max(TAG_MAX_LENGTH, `Use ${TAG_MAX_LENGTH} characters or fewer for a tag.`),
    )
    .max(MAX_DEFAULT_TAGS, `Use at most ${MAX_DEFAULT_TAGS} tags.`),
  /** `"off"` or a TTL in seconds. */
  cache: z.string(),
  /** A fault profile id; empty means none. Only the edit sheet shows this field. */
  faultProfileId: z.string(),
});

export type KeyFormValues = z.infer<typeof keyFormSchema>;

export const EMPTY_KEY_FORM: KeyFormValues = {
  name: "",
  environment: "",
  routeId: "",
  rpmLimit: "",
  tpmLimit: "",
  allowedModels: [],
  defaultTags: [],
  cache: CACHE_OFF,
  faultProfileId: "",
};

/** The edit sheet's starting values: what the key has now. */
export function formValuesFromKey(key: GatewayKey): KeyFormValues {
  return {
    name: key.name,
    environment: key.environment,
    routeId: key.route_id ?? "",
    rpmLimit: key.rpm_limit === null ? "" : String(key.rpm_limit),
    tpmLimit: key.tpm_limit === null ? "" : String(key.tpm_limit),
    allowedModels: key.allowed_models,
    defaultTags: key.default_tags,
    cache: cacheValueFromTtl(key.cache_ttl_seconds),
    faultProfileId: key.fault_profile_id ?? "",
  };
}

/* ---------- Fault profile rule ---------- */

export const FAULT_ON_PRODUCTION_REASON = "Fault profiles cannot attach to production keys";
export const DETACH_BEFORE_PRODUCTION =
  "Detach the fault profile before moving this key to production.";

/**
 * Whether the fault-profile selector is locked: a production key with no profile attached. A
 * production key that still has one stays open for exactly one change, to None.
 */
export function faultSelectDisabled(
  values: Pick<KeyFormValues, "environment" | "faultProfileId">,
): boolean {
  return isProductionEnvironment(values.environment) && values.faultProfileId === "";
}

/** Whether a profile is still chosen on a key whose environment says production. */
export function faultProductionConflict(values: KeyFormValues): boolean {
  return isProductionEnvironment(values.environment) && values.faultProfileId !== "";
}

/** The inline text and the Save-time error for that conflict. */
export function detachToSaveMessage(profileName: string): string {
  return `Production keys can't run faults. Detach ${profileName} to save.`;
}

/* ---------- API payloads ---------- */

export function toCreateInput(values: KeyFormValues): GatewayKeyCreate {
  return {
    name: values.name,
    environment: values.environment,
    route_id: values.routeId === "" ? null : values.routeId,
    rpm_limit: parseLimit(values.rpmLimit),
    tpm_limit: parseLimit(values.tpmLimit),
    cache_ttl_seconds: cacheTtlFromValue(values.cache),
    allowed_models: values.allowedModels,
    default_tags: values.defaultTags,
  };
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

/** Only what differs from the key, so an edit never resends (or clears) what it did not touch. */
export function toUpdateInput(values: KeyFormValues, key: GatewayKey): GatewayKeyUpdate {
  const update: GatewayKeyUpdate = {};
  if (values.name !== key.name) {
    update.name = values.name;
  }
  if (values.environment !== key.environment) {
    update.environment = values.environment;
  }
  if (values.routeId !== "" && values.routeId !== key.route_id) {
    update.route_id = values.routeId;
  }
  const rpm = parseLimit(values.rpmLimit);
  if (rpm !== key.rpm_limit) {
    update.rpm_limit = rpm;
  }
  const tpm = parseLimit(values.tpmLimit);
  if (tpm !== key.tpm_limit) {
    update.tpm_limit = tpm;
  }
  const ttl = cacheTtlFromValue(values.cache);
  if (ttl !== key.cache_ttl_seconds) {
    update.cache_ttl_seconds = ttl;
  }
  if (!sameList(values.allowedModels, key.allowed_models)) {
    update.allowed_models = values.allowedModels;
  }
  if (!sameList(values.defaultTags, key.default_tags)) {
    update.default_tags = values.defaultTags;
  }
  const faultProfileId = values.faultProfileId === "" ? null : values.faultProfileId;
  if (faultProfileId !== key.fault_profile_id) {
    update.fault_profile_id = faultProfileId;
  }
  return update;
}

export function widens(key: GatewayKey, changes: GatewayKeyUpdate): boolean {
  if (changes.route_id !== undefined && changes.route_id !== key.route_id) return true;
  if (changes.environment !== undefined && changes.environment !== key.environment) return true;
  for (const field of ["rpm_limit", "tpm_limit"] as const) {
    const previous = key[field];
    const proposed = changes[field];
    if (proposed !== undefined && previous !== null && (proposed === null || proposed > previous))
      return true;
  }
  const models = changes.allowed_models;
  return (
    models != null &&
    key.allowed_models.length > 0 &&
    (models.length === 0 || models.some((model) => !key.allowed_models.includes(model)))
  );
}
