import { Link } from "@tanstack/react-router";
import { ChevronRight, CircleCheck, Info, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Callout } from "@/components/callout";
import { CopyButton } from "@/components/copy-button";
import { Badge } from "@/components/ui/badge";
import { useProjectParams } from "@/features/shell/project-context";
import type { Span } from "@/lib/api";
import { cn } from "@/lib/utils";

import { MISMATCH_BODY, formatExactUsd, type StatusTone } from "./cost-explanation";

const CHIP_VARIANT = { ok: "success", warning: "warning", muted: "neutral" } as const;

/** The status chip after the amount in the card header. Text, not a control. */
export function StatusChip({ tone, title }: { tone: StatusTone; title: string }) {
  return (
    <Badge variant={CHIP_VARIANT[tone]} className="shrink-0">
      {title}
    </Badge>
  );
}

/** Figma "Status line" of a matching span: a green check and where the figure came from. */
export function MatchLine({ version }: { version: string | null }) {
  return (
    <p role="status" className="flex items-center gap-2 text-label font-medium text-success">
      <CircleCheck aria-hidden className="size-4 shrink-0" />
      {version === null
        ? "Recomputed from stored usage and price row"
        : `Recomputed from stored usage and price row ${version}`}
    </p>
  );
}

interface MismatchCalloutProps {
  recomputed: string | null;
  stored: string | null;
  spanId: string;
}

/** Figma "Callout / mismatch": both figures, the bug sentence and a way to report the span. */
export function MismatchCallout({ recomputed, stored, spanId }: MismatchCalloutProps) {
  const recomputedText = formatExactUsd(recomputed) ?? "an unknown figure";
  const storedText = formatExactUsd(stored) ?? "no figure";
  return (
    <Callout
      tone="warning"
      role="alert"
      title={`Recomputed ${recomputedText}, but the span stores ${storedText}`}
      action={<CopyButton value={spanId} label="Copy span id" showLabel variant="secondary" />}
    >
      {MISMATCH_BODY}
    </Callout>
  );
}

/** Figma "Explanation": the server's sentence for a status that has no lines to show. */
export function Explanation({ icon = Info, children }: { icon?: LucideIcon; children: ReactNode }) {
  const Icon = icon;
  return (
    <p className="flex items-start gap-2 text-sm text-muted-foreground">
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </p>
  );
}

/** Figma "Note": a small caption with an info icon (reasoning tokens, unreported usage). */
export function Note({ children, tile = false }: { children: ReactNode; tile?: boolean }) {
  return (
    <p
      className={cn(
        "flex items-start gap-2 text-xs font-medium text-muted-foreground",
        tile && "rounded-input bg-surface-muted p-3",
      )}
    >
      <Info aria-hidden className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </p>
  );
}

const LINK =
  "inline-flex items-center gap-1 rounded-sm text-sm font-semibold text-accent underline-offset-4 hover:underline focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring";

/**
 * Figma "Links" of the no-price state: Cost › Pricing lists the unpriced models, and opens "Add
 * price override" prefilled with the span's provider and model when both are known.
 */
export function NoPriceLinks({ provider, model }: Pick<Span, "provider" | "model">) {
  const { orgId, projectId } = useProjectParams();
  const params = { orgId, projectId };
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2">
      <Link to="/$orgId/$projectId/pricing" params={params} className={LINK}>
        See unpriced models
        <ChevronRight aria-hidden className="size-4" />
      </Link>
      <Link
        to="/$orgId/$projectId/pricing"
        params={params}
        search={provider !== null && model !== null ? { provider, model } : {}}
        className={LINK}
      >
        Add a price override
        <ChevronRight aria-hidden className="size-4" />
      </Link>
    </div>
  );
}
