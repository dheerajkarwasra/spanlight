import type { UseQueryResult } from "@tanstack/react-query";

import { Skeleton } from "@/components/ui/skeleton";
import { formatUsd, isZeroMoney } from "@/features/billing-sources/reconciliation-format";
import type { TokenMix } from "@/lib/api";

import {
  mixCaption,
  mixLabel,
  shareText,
  tokenShares,
  TOKEN_CLASS_LABELS,
  type TokenClass,
  type TokenShare,
} from "./cost-strip-data";

const COLORS: Record<TokenClass, string> = {
  input: "var(--chart-mix-input)",
  output: "var(--chart-mix-output)",
  cache_read: "var(--chart-mix-cache-read)",
  cache_write: "var(--chart-mix-cache-write)",
};

/**
 * The share of each token class as one stacked bar (spec ruling 3): 2 px surface gaps, 4 px
 * rounded ends. Two colours are under 3:1 on the light surface, so the legend always carries
 * the percentages beside them and the bar's name says them all.
 */
function TokenMixBar({ shares }: { shares: TokenShare[] }) {
  return (
    <div role="img" aria-label={mixLabel(shares)} className="flex h-2 w-full gap-0.5">
      {shares
        .filter((share) => share.tokens > 0)
        .map((share) => (
          <span
            key={share.key}
            className="min-w-0.5 basis-0 first:rounded-l-[4px] last:rounded-r-[4px]"
            style={{ flexGrow: share.tokens, backgroundColor: COLORS[share.key] }}
          />
        ))}
    </div>
  );
}

function Legend({ shares }: { shares: TokenShare[] }) {
  return (
    <ul aria-hidden className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-2xs text-muted-foreground">
      {shares.map((share) => (
        <li key={share.key} className="flex min-w-0 items-center gap-1.5">
          <span
            className="size-2 shrink-0 rounded-[2px]"
            style={{ backgroundColor: COLORS[share.key] }}
          />
          <span className="truncate tabular">
            {TOKEN_CLASS_LABELS[share.key]} {shareText(share)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** The telemetry spend card's detail from 768 px: token mix, fees and the days it covers. */
export function TokenMixPanel({ query }: { query: UseQueryResult<TokenMix> }) {
  if (query.isPending) {
    return <Skeleton className="mt-2 hidden h-2 w-full md:block" />;
  }
  if (query.isError) {
    return (
      <p className="mt-2 hidden text-2xs text-muted-foreground md:block">
        Couldn't load the token mix
      </p>
    );
  }
  const mix = query.data;
  const shares = tokenShares(mix);
  const fee = mix.fee_usd;
  return (
    <div className="mt-2 hidden flex-col gap-1.5 md:flex">
      {shares ? (
        <>
          <TokenMixBar shares={shares} />
          <Legend shares={shares} />
        </>
      ) : (
        <p className="text-2xs text-muted-foreground">No token counts for these days yet</p>
      )}
      {fee !== null && !isZeroMoney(fee) ? (
        <p className="text-xs text-muted-foreground tabular">Fees {formatUsd(fee)}</p>
      ) : null}
      <p className="text-2xs text-subtle-foreground">{mixCaption(mix)}</p>
    </div>
  );
}
