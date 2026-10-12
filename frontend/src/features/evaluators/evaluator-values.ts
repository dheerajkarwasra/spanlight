import type {
  Evaluator,
  EvaluatorConfig,
  EvaluatorConfigInput,
  EvaluatorCreate,
  EvaluatorFilters,
  EvaluatorUpdate,
  HeuristicCheck,
} from "@/lib/api";

import {
  caseSensitiveDefault,
  EMPTY_EVALUATOR_VALUES,
  EXPECTED_OUTPUT_RULES,
  splitNames,
  type EvaluatorFormValues,
} from "./evaluator-schema";

/* Form values to API bodies and back. */

function toCheck(values: EvaluatorFormValues): HeuristicCheck {
  switch (values.rule) {
    case "regex":
      return {
        rule: "regex",
        pattern: values.pattern,
        target: values.target,
        ignore_case: values.ignoreCase,
      };
    case "contains":
      return {
        rule: "contains",
        needle: values.needle,
        target: values.target,
        case_sensitive: values.caseSensitive,
      };
    case "json_valid":
      return { rule: "json_valid", target: values.target };
    case "length":
      return { rule: "length", target: values.target, unit: values.unit };
    case "exact_match":
      return {
        rule: "exact_match",
        normalize_whitespace: values.normalizeWhitespace,
        case_sensitive: values.caseSensitive,
      };
    case "levenshtein":
      return { rule: "levenshtein" };
  }
}

/** The request config: a judge names either a built-in template or a prompt version. */
export function toConfig(values: EvaluatorFormValues): EvaluatorConfigInput {
  if (values.kind === "heuristic") {
    return { kind: "heuristic", check: toCheck(values) };
  }
  if (values.kind === "webhook") {
    return { kind: "webhook", channel_id: values.channelId };
  }
  const temperature = values.temperature.trim();
  const common = {
    kind: "llm_judge" as const,
    credential_id: values.credentialId,
    model: values.model.trim(),
    max_tokens: Number(values.maxTokens),
    temperature: temperature === "" ? null : temperature,
  };
  return values.judgeSource === "template"
    ? { ...common, judge_template: values.judgeTemplate }
    : { ...common, prompt_version_id: values.promptVersionId };
}

export function toFilters(values: EvaluatorFormValues): EvaluatorFilters {
  const minimum = values.minDurationMs.trim();
  return {
    environments: values.environments,
    models: values.models,
    tags: values.tags,
    trace_names: splitNames(values.traceNames),
    min_duration_ms: minimum === "" ? null : Number(minimum),
  };
}

/** The decimal string the server stores, e.g. 5 → "0.05". */
export function toSamplingRate(percent: number): string {
  return String(Number((percent / 100).toFixed(4)));
}

/** A rule that needs an expected output never samples, whatever the slider was left at. */
function samplingOf(values: EvaluatorFormValues): number {
  return values.kind === "heuristic" && EXPECTED_OUTPUT_RULES.has(values.rule)
    ? 0
    : values.samplingPercent;
}

/** A stored rate ("0.0050") as the form's percent (0.5), without float noise. */
export function percentOf(rate: string): number {
  return Math.round(Number(rate) * 10_000) / 100;
}

function normalizedBudget(values: EvaluatorFormValues): string | null {
  if (values.kind !== "llm_judge") {
    return null;
  }
  const trimmed = values.budget.trim();
  const whole = trimmed.endsWith(".") ? trimmed.slice(0, -1) : trimmed;
  return whole.startsWith(".") ? `0${whole}` : whole;
}

export function toEvaluatorCreate(values: EvaluatorFormValues): EvaluatorCreate {
  return {
    name: values.name.trim(),
    config: toConfig(values),
    sampling_rate: toSamplingRate(samplingOf(values)),
    filters: toFilters(values),
    monthly_budget_usd: normalizedBudget(values),
  };
}

/** The members that changed since `initial`; `config` goes whole (minus `kind`) when it did. */
export function toEvaluatorUpdate(
  values: EvaluatorFormValues,
  initial: EvaluatorFormValues,
): EvaluatorUpdate {
  const update: EvaluatorUpdate = {};
  if (values.name.trim() !== initial.name) {
    update.name = values.name.trim();
  }
  if (samplingOf(values) !== samplingOf(initial)) {
    update.sampling_rate = toSamplingRate(samplingOf(values));
  }
  if (JSON.stringify(toFilters(values)) !== JSON.stringify(toFilters(initial))) {
    update.filters = toFilters(values);
  }
  const budget = normalizedBudget(values);
  if (budget !== null && budget !== normalizedBudget(initial)) {
    update.monthly_budget_usd = budget;
  }
  const config = toConfig(values);
  if (JSON.stringify(config) !== JSON.stringify(toConfig(initial))) {
    update.config = Object.fromEntries(Object.entries(config).filter(([key]) => key !== "kind"));
  }
  return update;
}

function checkValues(check: HeuristicCheck): Partial<EvaluatorFormValues> {
  const base = { rule: check.rule, caseSensitive: caseSensitiveDefault(check.rule) };
  switch (check.rule) {
    case "regex":
      return {
        ...base,
        pattern: check.pattern,
        target: check.target ?? "output",
        ignoreCase: check.ignore_case ?? false,
      };
    case "contains":
      return {
        ...base,
        needle: check.needle,
        target: check.target ?? "output",
        caseSensitive: check.case_sensitive ?? false,
      };
    case "json_valid":
      return { ...base, target: check.target ?? "output" };
    case "length":
      return { ...base, target: check.target ?? "output", unit: check.unit ?? "chars" };
    case "exact_match":
      return {
        ...base,
        normalizeWhitespace: check.normalize_whitespace ?? true,
        caseSensitive: check.case_sensitive ?? true,
      };
    case "levenshtein":
      return base;
  }
}

function configValues(config: EvaluatorConfig): Partial<EvaluatorFormValues> {
  if (config.kind === "heuristic") {
    return checkValues(config.check);
  }
  if (config.kind === "webhook") {
    return { channelId: config.channel_id };
  }
  return {
    judgeSource: "prompt",
    promptVersionId: config.prompt_version_id,
    credentialId: config.credential_id,
    model: config.model,
    maxTokens: String(config.max_tokens ?? 256),
    temperature:
      config.temperature === null || config.temperature === undefined
        ? ""
        : String(config.temperature),
  };
}

/** The form as an existing evaluator fills it; the judge's prompt name is resolved later. */
export function valuesFromEvaluator(evaluator: Evaluator): EvaluatorFormValues {
  const filters = evaluator.filters;
  return {
    ...EMPTY_EVALUATOR_VALUES,
    ...configValues(evaluator.config),
    name: evaluator.name,
    kind: evaluator.kind,
    budget: evaluator.monthly_budget_usd ?? "",
    samplingPercent: percentOf(evaluator.sampling_rate),
    environments: filters.environments ?? [],
    models: filters.models ?? [],
    tags: filters.tags ?? [],
    traceNames: (filters.trace_names ?? []).join(", "),
    minDurationMs:
      filters.min_duration_ms === null || filters.min_duration_ms === undefined
        ? ""
        : String(filters.min_duration_ms),
  };
}

/**
 * Problem `field` paths from the API → form fields. Pydantic puts the union tags in the path
 * (`config.heuristic.check.regex.pattern`, `config.llm_judge.temperature`), so they are dropped
 * before the lookup.
 */
export function evaluatorFieldOf(path: string): keyof EvaluatorFormValues | null {
  const plain = path
    .replace(/^config\.(heuristic|llm_judge|webhook)\./, "config.")
    .replace(/^config\.check\.[a-z_]+\.(pattern|needle)$/, "config.check.$1");
  const map: Record<string, keyof EvaluatorFormValues> = {
    name: "name",
    sampling_rate: "samplingPercent",
    monthly_budget_usd: "budget",
    "config.prompt_version_id": "promptVersionId",
    "config.judge_template": "judgeTemplate",
    "config.credential_id": "credentialId",
    "config.model": "model",
    "config.max_tokens": "maxTokens",
    "config.temperature": "temperature",
    "config.channel_id": "channelId",
    "config.check.pattern": "pattern",
    "config.check.needle": "needle",
  };
  return map[plain] ?? null;
}
