import { Tags, X } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { TileList, TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { shortDay } from "@/features/billing-sources/window-picker";
import { errorMessage, type PriceOverride } from "@/lib/api";

import { formatRate } from "./price-book";
import { useDeletePriceOverride, usePriceOverridesQuery } from "./pricing-queries";

/** Spec ruling 8: what deleting an override does and doesn't change. */
export const DELETE_OVERRIDE_NOTE =
  "Stored costs are not recomputed; new spans use the book price.";

/** "$0.80 in · $3.20 out · $0.20 cache read"; "— cache read" when the override has none. */
function ratesLine(override: PriceOverride): string {
  const cached = formatRate(override.cached_input_per_mtok) ?? "—";
  return `${formatRate(override.input_per_mtok)} in · ${formatRate(override.output_per_mtok)} out · ${cached} cache read`;
}

function originLine(override: PriceOverride): string {
  // The UTC day, as the dialog sends it and the price book shows it.
  const from = `From ${shortDay(override.effective_from.slice(0, 10))}`;
  return override.created_by ? `${from} · added by ${override.created_by.name}` : from;
}

interface OverridesCardProps {
  /** Opens the blank override dialog; null without `gateway:write`. */
  onAdd: (() => void) | null;
  className?: string;
}

/**
 * Spec ruling 8 "Organization overrides": the org's own prices, which replace the book price for
 * new spans. Every member reads them; `gateway:write` adds and deletes.
 */
export function OverridesCard({ onAdd, className }: OverridesCardProps) {
  const query = usePriceOverridesQuery();
  const remove = useDeletePriceOverride();
  const overrides = query.data;
  const canWrite = onAdd !== null;

  let body;
  if (query.isPending) {
    body = <TileListSkeleton label="Loading overrides" rows={2} className="h-[76px]" />;
  } else if (overrides === undefined) {
    body = (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load overrides"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  } else if (overrides.length === 0) {
    body = (
      <EmptyState
        icon={Tags}
        headingLevel={3}
        title="No overrides yet."
        description="An override replaces the book price for new spans."
        className="rounded-tile bg-surface-muted"
      />
    );
  } else {
    body = (
      <TileList label="Organization overrides">
        {overrides.map((override) => (
          <li
            key={override.id}
            className="flex items-start gap-3 rounded-tile bg-surface-muted px-4 py-3"
          >
            <div className="flex min-w-0 flex-1 flex-col gap-0.5 text-sm">
              <p className="[overflow-wrap:anywhere]">
                {override.provider}{" "}
                <span className="font-mono text-[13px] font-semibold">
                  {override.model_pattern}
                </span>
              </p>
              <p className="text-foreground tabular">{ratesLine(override)}</p>
              <p className="text-xs text-muted-foreground">{originLine(override)}</p>
            </div>
            {canWrite ? (
              <ConfirmDialog
                trigger={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Delete override for ${override.model_pattern}`}
                    className="max-md:size-11"
                  >
                    <X aria-hidden />
                  </Button>
                }
                title={`Delete the override for ${override.model_pattern}?`}
                description={DELETE_OVERRIDE_NOTE}
                confirmLabel="Delete override"
                onConfirm={async () => {
                  try {
                    await remove.mutateAsync(override.id);
                    toast.success(`Deleted the override for ${override.model_pattern}.`);
                  } catch (error) {
                    toast.error(errorMessage(error));
                    throw error;
                  }
                }}
              />
            ) : null}
          </li>
        ))}
      </TileList>
    );
  }

  return (
    <SectionCard
      title="Organization overrides"
      description="Replace the book price for new spans."
      actions={
        onAdd ? (
          <Button size="sm" onClick={onAdd} className="max-md:min-h-11">
            Add
          </Button>
        ) : null
      }
      className={className}
    >
      {body}
    </SectionCard>
  );
}
