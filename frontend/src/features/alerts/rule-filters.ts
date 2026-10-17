import type { AlertRuleFilters, ErrorClass, Metric, RateLimitKind } from "@/lib/api";

/*
 * The rule editor's filters. Environment, provider and model are free text ("" is any). Error
 * class and rate-limit kind narrow the failed calls counted, so the server accepts them on
 * `error_rate` and `error_calls` only (`422 VALIDATION_ERROR` "filters.error_class: applies to
 * error_rate and error_calls" otherwise): the editor shows them for those metrics and never sends
 * them for another, so a hidden filter can't apply or refuse a save.
 */

export type FilterField = "environment" | "provider" | "model";
export const FILTER_FIELDS: readonly FilterField[] = ["environment", "provider", "model"];

/** The metrics that take the error-class and rate-limit-kind filters. */
const ERROR_FILTER_METRICS: readonly Metric[] = ["error_rate", "error_calls"];

export function takesErrorFilters(metric: Metric): boolean {
  return ERROR_FILTER_METRICS.includes(metric);
}

/** The filter part of the form; "" means any. */
export interface RuleFilterValues {
  metric: Metric;
  environment: string;
  provider: string;
  model: string;
  errorClass: ErrorClass | "";
  rateLimitKind: RateLimitKind | "";
}

/** The request's `filters`: only the ones set, and the error filters only where they apply. */
export function filtersOf(values: RuleFilterValues): AlertRuleFilters {
  const filters: AlertRuleFilters = {};
  for (const field of FILTER_FIELDS) {
    const value = values[field].trim();
    if (value !== "") {
      filters[field] = value;
    }
  }
  if (takesErrorFilters(values.metric)) {
    if (values.errorClass !== "") {
      filters.error_class = values.errorClass;
    }
    if (values.rateLimitKind !== "") {
      filters.rate_limit_kind = values.rateLimitKind;
    }
  }
  return filters;
}

/** A stored rule's filters as form values. */
export function filterValuesOf(filters: AlertRuleFilters): Omit<RuleFilterValues, "metric"> {
  return {
    environment: filters.environment ?? "",
    provider: filters.provider ?? "",
    model: filters.model ?? "",
    errorClass: filters.error_class ?? "",
    rateLimitKind: filters.rate_limit_kind ?? "",
  };
}

const API_FILTER_FIELDS: Readonly<Record<string, keyof Omit<RuleFilterValues, "metric">>> = {
  environment: "environment",
  provider: "provider",
  model: "model",
  error_class: "errorClass",
  rate_limit_kind: "rateLimitKind",
};

/** The form field of a `filters.<name>` error path; Environment for an unknown name. */
export function filterFieldOf(name: string): keyof Omit<RuleFilterValues, "metric"> {
  return API_FILTER_FIELDS[name] ?? "environment";
}
