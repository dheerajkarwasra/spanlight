import type { ErrorClass } from "@/lib/api";

/**
 * Plain-language names for the failure classes, in the one order every view uses (the Provider
 * health stacking and legend, the Traces filter, the alert editor): provider capacity first, then
 * the rest, `unknown` last. It matches the backend's `ERROR_CLASS_ORDER`.
 */
const ERROR_CLASS_LABELS: Record<ErrorClass, string> = {
  rate_limit: "Rate limited",
  provider_5xx: "Provider error",
  timeout: "Timed out",
  network: "Network",
  auth: "Authentication",
  context_length: "Context too long",
  content_filter: "Content filtered",
  client: "Client error",
  gateway: "Gateway",
  unknown: "Unknown",
};

/**
 * Every class, in display order. Derived from the label map, which the compiler forces to name
 * each `ErrorClass`, so a class added to the API cannot be dropped from the URL or the menu
 * silently.
 */
export const ERROR_CLASSES = Object.keys(ERROR_CLASS_LABELS) as [ErrorClass, ...ErrorClass[]];

export function isErrorClass(value: string): value is ErrorClass {
  return (ERROR_CLASSES as readonly string[]).includes(value);
}

export function errorClassLabel(errorClass: ErrorClass): string {
  return ERROR_CLASS_LABELS[errorClass];
}

/**
 * The class's colour on every chart and key (`--chart-class-*`). Wherever a class carries a
 * swatch, the label next to it carries the meaning; the colour is decoration.
 */
export function errorClassColor(errorClass: ErrorClass): string {
  return `var(--chart-class-${errorClass.replaceAll("_", "-")})`;
}
