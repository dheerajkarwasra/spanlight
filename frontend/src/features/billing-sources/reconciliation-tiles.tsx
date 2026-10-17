import { Info } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import { FigureText, VarianceBadge } from "./reconciliation-figure";
import type { SummaryTile } from "./reconciliation-table";

const GRID = "grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5";
/** Settled days takes a row of its own on phones (Figma "Reconciliation / mobile 375"). */
const LAST_TILE = "col-span-2 md:col-span-1";
const TILE =
  "flex min-w-0 flex-col items-start gap-1.5 rounded-tile border border-border bg-surface px-5 py-[18px]";

/** What a figure means: a 24 px icon with a 44 px touch target that opens a short note. */
function InfoButton({ label, info }: { label: string; info: string }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`What ${label.toLowerCase()} means`}
          className="relative flex size-6 items-center justify-center rounded-full text-muted-foreground transition-colors before:absolute before:-inset-2.5 hover:text-foreground"
        >
          <Info aria-hidden className="size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" className="w-72 text-xs font-medium text-muted-foreground">
        {info}
      </PopoverContent>
    </Popover>
  );
}

function Tile({ tile, className }: { tile: SummaryTile; className?: string }) {
  return (
    <div className={cn(TILE, className)}>
      <div className="flex items-center gap-0.5">
        <p className="text-xs font-medium text-muted-foreground">{tile.label}</p>
        {tile.info ? <InfoButton label={tile.label} info={tile.info} /> : null}
      </div>
      <p
        className={cn(
          "w-full text-[1.75rem] leading-[1.2] font-extrabold tracking-[-0.03em] [overflow-wrap:anywhere]",
          tile.value === null ? "text-subtle-foreground" : "text-foreground",
        )}
      >
        <FigureText figure={{ text: tile.value, reason: tile.reason }} />
      </p>
      {tile.badge ? (
        <VarianceBadge
          text={tile.badge.text}
          tone={tile.badge.tone}
          accessibleName={tile.badge.accessibleName}
        />
      ) : null}
      <p className="w-full text-xs font-medium text-subtle-foreground">{tile.detail}</p>
    </div>
  );
}

/**
 * Figma "Bet1/Summary tile" ×5: provider, Spanlight, explained, residual with its variance band,
 * and settled days. Stat tiles, not a chart (dataviz): the value in proportional figures, the
 * band in words beside its colour, an unknown value as "—" with its reason in the detail line.
 */
export function ReconciliationTiles({ tiles }: { tiles: SummaryTile[] }) {
  return (
    <section aria-label="Summary" className={GRID}>
      {tiles.map((tile, index) => (
        <Tile
          key={tile.key}
          tile={tile}
          className={index === tiles.length - 1 ? LAST_TILE : undefined}
        />
      ))}
    </section>
  );
}

/** Figma "Reconciliation / loading": the five tiles as shimmering placeholders. */
export function ReconciliationTilesSkeleton() {
  return (
    <div aria-hidden className={GRID}>
      {Array.from({ length: 5 }, (_, index) => (
        <div key={index} className={cn(TILE, "gap-3 py-5", index === 4 && LAST_TILE)}>
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-7 w-28" />
          <Skeleton className="h-3 w-full" />
        </div>
      ))}
    </div>
  );
}
