import { CircleCheck } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { RowAction, TileList, TileListSkeleton } from "@/components/tile-list";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableRowHeader,
} from "@/components/ui/table";
import { UnknownValue } from "@/components/unknown-value";
import type { UnpricedModel } from "@/lib/api";
import { formatCompact, formatInteger } from "@/lib/format";

import type { OverridePrefill } from "./price-override-form";
import { useUnpricedModelsQuery } from "./pricing-queries";

const NO_PROVIDER = "The calls didn't report a provider.";

interface UnpricedModelsCardProps {
  /** Opens the override dialog for a row; null without `gateway:write`. */
  onAddPrice: ((prefill: OverridePrefill) => void) | null;
  className?: string;
}

/**
 * Spec ruling 8 "Unpriced models": this project's models with usage but no price in the page's
 * window and environment, most-called first. A table from `md`, tiles below.
 */
export function UnpricedModelsCard({ onAddPrice, className }: UnpricedModelsCardProps) {
  const query = useUnpricedModelsQuery();
  const models = query.data;

  const addPrice = (model: UnpricedModel) =>
    onAddPrice ? (
      <RowAction
        aria-label={`Add price for ${model.model}`}
        onClick={() => {
          onAddPrice({ provider: model.provider ?? undefined, model_pattern: model.model });
        }}
        className="max-md:min-h-11"
      >
        Add price
      </RowAction>
    ) : null;

  let body;
  if (query.isPending) {
    body = <TileListSkeleton label="Loading unpriced models" rows={3} />;
  } else if (models === undefined) {
    body = (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load unpriced models"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  } else if (models.length === 0) {
    body = (
      <EmptyState
        icon={CircleCheck}
        headingLevel={3}
        title="Every model in this window has a price."
        description="Calls without a price would show here with their token counts."
        className="rounded-tile bg-surface-muted"
      />
    );
  } else {
    body = (
      <div
        aria-busy={query.isPlaceholderData || undefined}
        className={query.isPlaceholderData ? "opacity-60" : undefined}
      >
        <Table aria-label="Unpriced models" containerClassName="hidden md:block">
          <TableHeader>
            <TableRow>
              <TableHead>Provider</TableHead>
              <TableHead>Model</TableHead>
              <TableHead className="text-right">Calls</TableHead>
              <TableHead className="text-right">Input</TableHead>
              <TableHead className="text-right">Output</TableHead>
              <TableHead>
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {models.map((model) => (
              <TableRow key={`${model.provider ?? ""}/${model.model}`}>
                <TableCell>{model.provider ?? <UnknownValue reason={NO_PROVIDER} />}</TableCell>
                <TableRowHeader className="font-mono text-[13px] [overflow-wrap:anywhere]">
                  {model.model}
                </TableRowHeader>
                <TableCell className="text-right tabular">
                  {formatInteger(model.llm_calls)}
                </TableCell>
                <TableCell className="text-right tabular">
                  {formatCompact(model.input_tokens)}
                </TableCell>
                <TableCell className="text-right tabular">
                  {formatCompact(model.output_tokens)}
                </TableCell>
                <TableCell className="text-right">{addPrice(model)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <TileList label="Unpriced models" className="md:hidden">
          {models.map((model) => (
            <li
              key={`${model.provider ?? ""}/${model.model}`}
              className="flex items-center gap-3 rounded-tile bg-surface-muted px-4 py-3"
            >
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <p className="font-mono text-[13px] font-semibold [overflow-wrap:anywhere]">
                  {model.model}
                </p>
                <p className="text-xs text-muted-foreground">
                  {model.provider ?? "No provider"} · {formatInteger(model.llm_calls)} calls ·{" "}
                  {formatCompact(model.input_tokens)} in / {formatCompact(model.output_tokens)} out
                </p>
              </div>
              {addPrice(model)}
            </li>
          ))}
        </TileList>
      </div>
    );
  }

  return (
    <SectionCard
      title="Unpriced models"
      description="Calls in this window and environment with no price. Their cost shows as “—”."
      className={className}
    >
      {body}
    </SectionCard>
  );
}
