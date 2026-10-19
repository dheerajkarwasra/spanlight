import { ValueOrUnknown } from "@/components/unknown-value";
import { NO_PROVIDER_REASON } from "@/lib/provider-label";
import type { ModelMetrics } from "@/lib/api";
import { formatCompact, formatInteger, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ModelCost, ModelLatency, ModelName, NoProviderTag } from "./model-cells";
import { formatProvider, modelErrorRate, modelRowKey } from "./models";

const HEAD_CELL = "py-2.5 text-left text-overline font-semibold text-subtle-foreground uppercase";
const NUMERIC_HEAD = cn(HEAD_CELL, "text-right");

/** The real table, shown when the card is wide. */
export function ModelsTable({ models }: { models: ModelMetrics[] }) {
  return (
    <table className="hidden w-full border-separate border-spacing-0 @4xl:table">
      <caption className="sr-only">Models, busiest first</caption>
      <thead>
        <tr className="[&>th]:bg-surface-muted [&>th+th]:pl-3 [&>th:first-child]:rounded-l-tile [&>th:first-child]:pl-5 [&>th:last-child]:rounded-r-tile [&>th:last-child]:pr-5">
          <th scope="col" className={HEAD_CELL}>
            Model
          </th>
          <th scope="col" className={cn(HEAD_CELL, "hidden w-[116px] @5xl:table-cell")}>
            Provider
          </th>
          <th scope="col" className={cn(NUMERIC_HEAD, "w-[92px]")}>
            Calls
          </th>
          <th scope="col" className={cn(NUMERIC_HEAD, "w-[116px]")}>
            Errors
          </th>
          <th scope="col" className={cn(NUMERIC_HEAD, "w-[84px]")}>
            p50
          </th>
          <th scope="col" className={cn(NUMERIC_HEAD, "w-[84px]")}>
            p95
          </th>
          <th scope="col" className={cn(NUMERIC_HEAD, "w-[108px]")}>
            Tokens in
          </th>
          <th scope="col" className={cn(NUMERIC_HEAD, "w-[108px]")}>
            Tokens out
          </th>
          <th scope="col" className={cn(NUMERIC_HEAD, "w-[124px]")}>
            Cost
          </th>
        </tr>
      </thead>
      <tbody>
        {models.map((model) => (
          <ModelRow key={modelRowKey(model)} model={model} />
        ))}
      </tbody>
    </table>
  );
}

function ModelRow({ model }: { model: ModelMetrics }) {
  const errorRate = modelErrorRate(model);
  return (
    <tr className="h-14 [&:last-child>td]:border-b-0 [&>td]:border-b [&>td]:border-border [&>td+td]:pl-3 [&>td:first-child]:pl-5 [&>td:last-child]:pr-5">
      <td className="max-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 truncate">
            <ModelName model={model.model} />
          </span>
          {model.provider === null ? <NoProviderTag className="@5xl:hidden" /> : null}
        </div>
      </td>
      <td className="hidden text-sm text-muted-foreground @5xl:table-cell">
        <ValueOrUnknown value={formatProvider(model.provider)} reason={NO_PROVIDER_REASON} />
      </td>
      <td className="text-right text-sm font-medium text-foreground tabular">
        {formatInteger(model.calls)}
      </td>
      <td className="text-right whitespace-nowrap tabular">
        <span
          className={cn(
            "text-sm font-medium",
            model.errors > 0 ? "text-danger-text" : "text-muted-foreground",
          )}
        >
          {formatInteger(model.errors)}
        </span>
        {errorRate !== null && model.errors > 0 ? (
          <span className="ml-1.5 text-xs font-medium text-subtle-foreground">
            {formatPercent(errorRate)}
          </span>
        ) : null}
      </td>
      <td className="text-right text-sm text-muted-foreground tabular">
        <ModelLatency value={model.p50_ms} approximate={model.approximate} />
      </td>
      <td className="text-right text-sm font-medium text-foreground tabular">
        <ModelLatency value={model.p95_ms} approximate={model.approximate} />
      </td>
      <td className="text-right text-sm text-muted-foreground tabular">
        {formatCompact(model.input_tokens)}
      </td>
      <td className="text-right text-sm text-muted-foreground tabular">
        {formatCompact(model.output_tokens)}
      </td>
      <td className="text-right text-sm font-semibold text-foreground tabular">
        <ModelCost model={model} />
      </td>
    </tr>
  );
}
