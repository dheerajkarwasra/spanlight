import type { ModelMetrics } from "@/lib/api";
import { parseMoney } from "@/lib/format";
import { providerLabel } from "@/lib/provider-label";

/** How many models the Models card lists before "All models" expands it. */
export const MODEL_PREVIEW_LIMIT = 5;

/** Busiest models first; ties broken by name so the order is stable between refetches. */
export function sortModelsByCalls(models: readonly ModelMetrics[]): ModelMetrics[] {
  return [...models].sort((a, b) => {
    if (b.calls !== a.calls) {
      return b.calls - a.calls;
    }
    return (a.model ?? "").localeCompare(b.model ?? "");
  });
}

/** Error ratio for a model row, or null when it made no calls. */
export function modelErrorRate(model: Pick<ModelMetrics, "calls" | "errors">): number | null {
  return model.calls > 0 ? model.errors / model.calls : null;
}

/** A stable React key for a model row; provider and model can both be null. */
export function modelRowKey(model: Pick<ModelMetrics, "provider" | "model">): string {
  return `${model.provider ?? "∅"}/${model.model ?? "∅"}`;
}

/** The display name for a provider id ("openai" → "OpenAI"); null when the call named none. */
export function formatProvider(provider: string | null): string | null {
  if (provider === null || provider.trim() === "") {
    return null;
  }
  return providerLabel(provider);
}

/** True when the model has no price, so its calls are missing from spend. */
export function isUnpriced(model: Pick<ModelMetrics, "cost_usd">): boolean {
  return parseMoney(model.cost_usd) === null;
}

/** Calls made with models that have no price. */
export function unpricedCalls(models: readonly ModelMetrics[]): number {
  return models.filter(isUnpriced).reduce((sum, model) => sum + model.calls, 0);
}
