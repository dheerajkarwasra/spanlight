import type { LinkProps } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { KpiCard } from "@/components/kpi-card";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { VarianceTone } from "@/features/traces";
import { useProjectParams } from "@/features/shell/project-context";
import type { AttributionQuery } from "@/lib/api";
import { cn } from "@/lib/utils";

import { TONE_ICONS, type CardLink, type StripCard } from "./cost-strip-data";
import { TokenMixPanel } from "./token-mix-bar";
import { useCostStrip } from "./use-cost-strip";

const LINKS = {
  spend: { to: "/$orgId/$projectId/attribution", spoken: "open spend" },
  reconciliation: { to: "/$orgId/$projectId/reconciliation", spoken: "open reconciliation" },
  pricing: { to: "/$orgId/$projectId/pricing", spoken: "open pricing" },
  "billing-sources": {
    to: "/$orgId/$projectId/settings/billing-sources",
    spoken: "open billing sources",
  },
} as const satisfies Record<CardLink, { to: LinkProps["to"]; spoken: string }>;

interface CostStripProps {
  /** The page's own attribution query (Spend), so the strip reads the same cache entry. */
  attributionQuery?: Omit<AttributionQuery, "cursor">;
  className?: string;
}

/**
 * The Cost hub's header (spec §3.2): telemetry spend, provider billed, variance and unpriced
 * calls, each a link to where it is explained. Each card loads and fails on its own. Below
 * 768 px the cards are compact (label, value, one line).
 */
export function CostStrip({ attributionQuery, className }: CostStripProps) {
  const { orgId, projectId } = useProjectParams();
  const strip = useCostStrip(attributionQuery);

  return (
    <ul
      aria-label="Cost summary"
      className={cn("grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4", className)}
    >
      {strip.cards.map((card) => (
        <li key={card.id} className="min-w-0">
          {card.pending ? (
            <CardSkeleton />
          ) : (
            <StripCardView
              card={card}
              href={
                card.href ? { to: LINKS[card.href].to, params: { orgId, projectId } } : undefined
              }
              spoken={card.href ? LINKS[card.href].spoken : null}
              onRetry={
                card.id === "spend" || card.id === "unpriced"
                  ? strip.retry.spend
                  : strip.retry.billing
              }
            >
              {card.id === "spend" && card.value !== null ? (
                <TokenMixPanel query={strip.tokenMix} />
              ) : null}
            </StripCardView>
          )}
        </li>
      ))}
    </ul>
  );
}

interface StripCardViewProps {
  card: StripCard;
  href: LinkProps | undefined;
  spoken: string | null;
  onRetry: () => void;
  children?: ReactNode;
}

function StripCardView({ card, href, spoken, onRetry, children }: StripCardViewProps) {
  const [first, ...rest] = card.lines;
  const suffix = [card.spoken, spoken].filter(Boolean).join(", ");
  const value =
    card.value === null ? null : (
      // Compact on phones; wraps rather than overflowing a half-width card.
      <span className="text-xl whitespace-normal md:[font-size:inherit]">
        {card.lowerBound ? (
          <>
            <span aria-hidden>≥ </span>
            <span className="sr-only">at least </span>
          </>
        ) : null}
        {card.value}
        {suffix ? <span className="sr-only">, {suffix}</span> : null}
      </span>
    );

  return (
    <KpiCard
      label={card.label}
      value={value}
      unknownReason={card.unknownReason}
      chip={card.freshness ? { freshness: card.freshness } : undefined}
      denominator={
        first === undefined ? undefined : (
          <FirstLine text={first} tone={card.tone} onRetry={card.retry ? onRetry : undefined} />
        )
      }
      href={href}
    >
      {rest.map((line) => (
        <p key={line} className="hidden truncate text-xs text-muted-foreground tabular md:block">
          {line}
        </p>
      ))}
      {children}
    </KpiCard>
  );
}

function FirstLine({
  text,
  tone,
  onRetry,
}: {
  text: string;
  tone: VarianceTone | undefined;
  onRetry: (() => void) | undefined;
}) {
  const band = tone && tone !== "unknown" ? TONE_ICONS[tone] : null;
  return (
    <span className="inline-flex max-w-full items-center gap-1">
      {/* The band's colour sits on the icon; the words carry it in text tokens (1.4.1). */}
      {band ? <band.icon aria-hidden className={cn("size-3.5 shrink-0", band.color)} /> : null}
      <span className="truncate">{text}</span>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="min-h-6 rounded-sm px-1 font-medium text-accent hover:underline focus-visible:outline-[1.5px] focus-visible:outline-ring"
        >
          Retry
        </button>
      ) : null}
    </span>
  );
}

function CardSkeleton() {
  return (
    <Card aria-hidden className="flex h-full min-h-[110px] flex-col gap-2 p-4">
      <Skeleton className="h-3 w-20" />
      <Skeleton className="h-7 w-24" />
      <Skeleton className="h-3 w-32" />
    </Card>
  );
}
