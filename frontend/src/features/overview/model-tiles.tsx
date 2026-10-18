import { Info } from "lucide-react";
import type { ReactNode } from "react";

import type { ModelMetrics } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ModelCost, ModelLatency, ModelName } from "./model-cells";
import { modelRowKey, unpricedCalls } from "./models";

/** One hairline-divided row per model, shown when the card is narrow. */
export function ModelTiles({ models }: { models: ModelMetrics[] }) {
  const unpriced = unpricedCalls(models);
  return (
    <div className="mt-2 flex flex-col gap-2 px-2 sm:px-5 @4xl:hidden">
      <ul className="grid gap-x-6 @xl:grid-cols-2 @3xl:grid-cols-3">
        {models.map((model) => (
          <li key={modelRowKey(model)} className="flex flex-col gap-3 border-t border-border py-4">
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate">
                <ModelName model={model.model} />
              </span>
              <span className="shrink-0 text-sm font-semibold text-foreground tabular">
                <ModelCost model={model} />
              </span>
            </div>
            <dl className="grid grid-cols-3 gap-2">
              <TileStat label="Calls">{formatInteger(model.calls)}</TileStat>
              <TileStat label="p95">
                <ModelLatency value={model.p95_ms} approximate={model.approximate} />
              </TileStat>
              <TileStat label="Errors" danger={model.errors > 0}>
                {formatInteger(model.errors)}
              </TileStat>
            </dl>
          </li>
        ))}
      </ul>
      {unpriced > 0 ? (
        <p className="flex items-start gap-2 px-2 pt-1.5 pb-1 text-xs font-medium text-muted-foreground">
          <Info aria-hidden className="mt-px size-3.5 shrink-0" />
          <span>
            — means no price for this model. Its {formatInteger(unpriced)}{" "}
            {unpriced === 1 ? "call isn’t" : "calls aren’t"} in spend.
          </span>
        </p>
      ) : null}
    </div>
  );
}

function TileStat({
  label,
  danger = false,
  children,
}: {
  label: string;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-overline text-subtle-foreground uppercase">{label}</dt>
      <dd
        className={cn(
          "truncate text-sm font-medium tabular",
          danger ? "text-danger-text" : "text-foreground",
        )}
      >
        {children}
      </dd>
    </div>
  );
}
