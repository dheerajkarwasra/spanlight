import { z } from "zod";

import type {
  AlertRule,
  AlertRuleInput,
  AlertRuleKind,
  AlertRulePreviewInput,
  Comparator,
  ErrorClass,
  Metric,
  RateLimitKind,
} from "@/lib/api";
import { ERROR_CLASSES } from "@/lib/error-class";
import { RATE_LIMIT_KIND_ORDER } from "@/lib/rate-limit-kind";

import { METRICS } from "./metric-labels";
import { FILTER_FIELDS, filterFieldOf, filtersOf, filterValuesOf } from "./rule-filters";
import { fromApiThreshold, thresholdError, toApiThreshold } from "./threshold-units";

/*
 * The rule editor's form. The bounds and kind rules mirror the server's `RuleSpec`
 * (`app/alerts/rule_spec.py`): a name of 1 to 80 characters; a threshold rule needs a threshold of
 * 0 or more and takes no baseline; an anomaly rule needs 3 to 30 baseline windows, takes a
 * sensitivity of 0.5 to 5.0 (3.0 by default) and no threshold; windows of 5 to 1440 minutes; a
 * cooldown of 0 to 1440 minutes (15 by default); filter values up to 200 characters; at most 10
 * channels. Numbers are kept as typed strings so a half-typed value never turns into a 0.
 */

export const NAME_MAX_LENGTH = 80;
export const FILTER_MAX_LENGTH = 200;
export const MAX_RULE_CHANNELS = 10;
export const WINDOW_BOUNDS = { min: 5, max: 1440 } as const;
export const BASELINE_BOUNDS = { min: 3, max: 30 } as const;
export const COOLDOWN_BOUNDS = { min: 0, max: 1440 } as const;
export const DEFAULT_SENSITIVITY = "3.0";
export const DEFAULT_BASELINE_WINDOWS = "12";
export const DEFAULT_COOLDOWN = "15";

export interface RuleFormValues {
  name: string;
  kind: AlertRuleKind;
  metric: Metric;
  comparator: Comparator;
  /** In the editor's unit (percent, seconds, dollars, a count); see `threshold-units.ts`. */
  threshold: string;
  windowMinutes: number;
  baselineWindows: string;
  sensitivity: string;
  /** "" means any. */
  environment: string;
  provider: string;
  model: string;
  /** "" means any; sent for `error_rate` and `error_calls` only. */
  errorClass: ErrorClass | "";
  rateLimitKind: RateLimitKind | "";
  cooldownMinutes: string;
  enabled: boolean;
  channelIds: string[];
}

export type RuleFormField = keyof RuleFormValues;
export { FILTER_FIELDS, type FilterField } from "./rule-filters";
type Issue = readonly [RuleFormField, string];

export const EMPTY_RULE_VALUES: RuleFormValues = {
  name: "",
  kind: "threshold",
  metric: "error_rate",
  comparator: "gt",
  threshold: "",
  windowMinutes: 15,
  baselineWindows: DEFAULT_BASELINE_WINDOWS,
  sensitivity: DEFAULT_SENSITIVITY,
  environment: "",
  provider: "",
  model: "",
  errorClass: "",
  rateLimitKind: "",
  cooldownMinutes: DEFAULT_COOLDOWN,
  enabled: true,
  channelIds: [],
};

const WHOLE_NUMBER = /^\d+$/;
const SENSITIVITY = /^\d+(\.\d{1,2})?$/;

function wholeInRange(value: string, bounds: { min: number; max: number }): boolean {
  const text = value.trim();
  if (!WHOLE_NUMBER.test(text)) {
    return false;
  }
  const parsed = Number(text);
  return parsed >= bounds.min && parsed <= bounds.max;
}

function sensitivityValid(value: string): boolean {
  const text = value.trim();
  return SENSITIVITY.test(text) && Number(text) >= 0.5 && Number(text) <= 5;
}

/**
 * What the preview needs to be right: what the rule measures and when it breaches. The name,
 * channels, cooldown and the enabled switch don't change the preview, so they never block it.
 */
export function definitionIssues(values: RuleFormValues): Issue[] {
  const issues: Issue[] = [];
  if (values.kind === "threshold") {
    const error = thresholdError(values.metric, values.threshold);
    if (error) {
      issues.push(["threshold", error]);
    }
  } else {
    if (!wholeInRange(values.baselineWindows, BASELINE_BOUNDS)) {
      issues.push(["baselineWindows", "Use 3 to 30 windows."]);
    }
    if (!sensitivityValid(values.sensitivity)) {
      issues.push(["sensitivity", "Use 0.5 to 5.0, with at most 2 decimals."]);
    }
  }
  if (!wholeInRange(String(values.windowMinutes), WINDOW_BOUNDS)) {
    issues.push(["windowMinutes", "Use 5 to 1440 minutes."]);
  }
  for (const field of FILTER_FIELDS) {
    if (values[field].trim().length > FILTER_MAX_LENGTH) {
      issues.push([field, `Use at most ${FILTER_MAX_LENGTH} characters.`]);
    }
  }
  return issues;
}

/** Every reason the form can't be saved, one per field. */
export function ruleFormIssues(values: RuleFormValues): Issue[] {
  const issues: Issue[] = [];
  const name = values.name.trim();
  if (name === "") {
    issues.push(["name", "Enter a name for this rule."]);
  } else if (name.length > NAME_MAX_LENGTH) {
    issues.push(["name", `Use at most ${NAME_MAX_LENGTH} characters.`]);
  }
  issues.push(...definitionIssues(values));
  if (!wholeInRange(values.cooldownMinutes, COOLDOWN_BOUNDS)) {
    issues.push(["cooldownMinutes", "Use 0 to 1440 minutes."]);
  }
  if (values.channelIds.length > MAX_RULE_CHANNELS) {
    issues.push(["channelIds", `Pick at most ${MAX_RULE_CHANNELS} channels.`]);
  }
  return issues;
}

export const ruleFormSchema = z
  .object({
    name: z.string(),
    kind: z.enum(["threshold", "anomaly"]),
    metric: z.enum(METRICS as [Metric, ...Metric[]]),
    comparator: z.enum(["gt", "gte", "lt", "lte"]),
    threshold: z.string(),
    windowMinutes: z.number(),
    baselineWindows: z.string(),
    sensitivity: z.string(),
    environment: z.string(),
    provider: z.string(),
    model: z.string(),
    errorClass: z.union([z.enum(ERROR_CLASSES), z.literal("")]),
    rateLimitKind: z.union([z.enum(RATE_LIMIT_KIND_ORDER), z.literal("")]),
    cooldownMinutes: z.string(),
    enabled: z.boolean(),
    channelIds: z.array(z.string()),
  })
  .superRefine((values, context) => {
    for (const [field, message] of ruleFormIssues(values)) {
      context.addIssue({ code: "custom", path: [field], message });
    }
  });

/**
 * The preview's body: what the rule measures and when it breaches. Fields that don't apply to
 * the kind are sent as null. The cooldown and the enabled switch are left out (the server's
 * defaults apply), so changing them neither asks for a new preview nor counts against the
 * preview rate limit. Call only once `definitionIssues` is empty.
 */
export function toPreviewInput(values: RuleFormValues): AlertRulePreviewInput {
  const threshold = values.kind === "threshold";
  return {
    kind: values.kind,
    metric: values.metric,
    comparator: values.comparator,
    threshold: threshold ? toApiThreshold(values.metric, values.threshold) : null,
    window_minutes: values.windowMinutes,
    baseline_windows: threshold ? null : Number(values.baselineWindows.trim()),
    sensitivity: threshold ? null : values.sensitivity.trim(),
    filters: filtersOf(values),
  };
}

/** The create and edit body (`RuleSpec`). An edit sends the whole rule again and replaces it. */
export function toRuleInput(values: RuleFormValues): AlertRuleInput {
  return {
    ...toPreviewInput(values),
    cooldown_minutes: Number(values.cooldownMinutes.trim()),
    enabled: values.enabled,
    name: values.name.trim(),
    channel_ids: [...new Set(values.channelIds)],
  };
}

/** The form for editing a rule as stored. */
export function valuesFromRule(rule: AlertRule): RuleFormValues {
  return {
    name: rule.name,
    kind: rule.kind,
    metric: rule.metric,
    comparator: rule.comparator,
    threshold: fromApiThreshold(rule.metric, rule.threshold),
    windowMinutes: rule.window_minutes,
    baselineWindows: rule.baseline_windows?.toString() ?? DEFAULT_BASELINE_WINDOWS,
    sensitivity: rule.sensitivity ?? DEFAULT_SENSITIVITY,
    ...filterValuesOf(rule.filters),
    cooldownMinutes: String(rule.cooldown_minutes),
    enabled: rule.enabled,
    channelIds: [...rule.channel_ids],
  };
}

const API_FIELDS: Readonly<Record<string, RuleFormField>> = {
  name: "name",
  kind: "kind",
  metric: "metric",
  comparator: "comparator",
  threshold: "threshold",
  window_minutes: "windowMinutes",
  baseline_windows: "baselineWindows",
  sensitivity: "sensitivity",
  cooldown_minutes: "cooldownMinutes",
  channel_ids: "channelIds",
};

/** The form field a server error path names (`filters.model` → `model`), or null. */
export function ruleFieldOf(path: string): RuleFormField | null {
  const [head = "", second = ""] = path.split(".");
  if (head === "filters") {
    return filterFieldOf(second);
  }
  return API_FIELDS[head] ?? null;
}
