import { getRouteApi } from "@tanstack/react-router";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { CostStrip } from "@/features/attribution/cost-strip";
import { usePermission } from "@/features/shell";

import { OverridesCard } from "./overrides-card";
import { PriceBookTable } from "./price-book-table";
import { PriceOverrideDialog } from "./price-override-dialog";
import type { OverridePrefill } from "./price-override-form";
import { UnpricedModelsCard } from "./unpriced-models-card";

const pricingRoute = getRouteApi("/_authed/$orgId/$projectId/pricing");

/**
 * Figma "Pricing" (714:348, 375: 714:1649), spec §3.4: the cost strip, this window's unpriced
 * models beside the organization's overrides, then the price book. `?provider=&model=` opens the
 * override dialog prefilled; closing it clears those params.
 */
export function PricingPage() {
  const canWrite = usePermission("gateway:write");
  const search = pricingRoute.useSearch();
  const navigate = pricingRoute.useNavigate();
  const fromUrl: OverridePrefill | null =
    search.provider !== undefined || search.model !== undefined
      ? { provider: search.provider, model_pattern: search.model }
      : null;
  // A dialog opened on the page; otherwise the URL's prefill decides.
  const [opened, setOpened] = useState<{ initial?: OverridePrefill } | null>(null);
  const dialog = opened ?? (fromUrl ? { initial: fromUrl } : null);

  function close() {
    setOpened(null);
    if (fromUrl) {
      void navigate({
        search: (prev) => ({ ...prev, provider: undefined, model: undefined }),
        replace: true,
      });
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Pricing"
        description="The prices Spanlight uses to cost each call, per 1M tokens. An organization override replaces the book price for new spans."
      />
      <CostStrip />
      <div className="grid items-start gap-6 xl:grid-cols-12">
        <UnpricedModelsCard
          onAddPrice={canWrite ? (initial) => setOpened({ initial }) : null}
          className="xl:col-span-8"
        />
        <OverridesCard onAdd={canWrite ? () => setOpened({}) : null} className="xl:col-span-4" />
      </div>
      <PriceBookTable />
      <PriceOverrideDialog
        // A new prefill opens a fresh form.
        key={JSON.stringify(dialog?.initial ?? null)}
        open={dialog !== null}
        onOpenChange={(next) => {
          if (!next) {
            close();
          }
        }}
        initial={dialog?.initial}
      />
    </div>
  );
}
