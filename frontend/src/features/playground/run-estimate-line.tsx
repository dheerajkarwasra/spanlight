import { Link } from "@tanstack/react-router";
import { ArrowUpRight, Info } from "lucide-react";

import { Button } from "@/components/ui/button";
import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { useTapTooltip } from "@/lib/use-tap-tooltip";

import { COLUMN_LETTERS } from "./config-state";
import { formatEstimateUsd, type RunEstimate } from "./run-estimate";

export const ESTIMATE_METHOD =
  "Input ≈ characters ÷ 3; output up to each column's max tokens. Org price overrides and cached-token discounts aren't included.";

/** Figma "Estimate line": the run's list-price ceiling, or the first column it is unknown for. */
export function RunEstimateLine({ estimate }: { estimate: RunEstimate }) {
  const { rootProps, triggerProps } = useTapTooltip();
  const first = "unknown" in estimate ? estimate.unknown[0] : undefined;
  return (
    <div className="flex items-center gap-0.5 text-xs text-muted-foreground">
      {first ? (
        <p>
          Est. unavailable for Run {COLUMN_LETTERS[first.column]} (
          {first.reason === "no_price" ? (
            <>
              no price for <code className="font-mono">{first.model}</code>
            </>
          ) : (
            "no max tokens set"
          )}
          )
        </p>
      ) : "usd" in estimate ? (
        <p className="tabular">Est. up to {formatEstimateUsd(estimate.usd)} at list prices</p>
      ) : null}
      <TooltipRoot {...rootProps}>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="How the estimate works"
            className="size-6 text-muted-foreground [&_svg]:size-3.5"
            {...triggerProps}
          >
            <Info aria-hidden />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom" collisionPadding={16}>
          {ESTIMATE_METHOD}
        </TooltipContent>
      </TooltipRoot>
    </div>
  );
}

/** The traces the playground produced: they all carry the `spanlight:playground` tag. */
export function PlaygroundRunsLink({ orgId, projectId }: { orgId: string; projectId: string }) {
  return (
    <Button variant="ghost" asChild>
      <Link
        to="/$orgId/$projectId/traces"
        params={{ orgId, projectId }}
        search={{ tag: "spanlight:playground" }}
      >
        View playground runs
        <ArrowUpRight aria-hidden />
      </Link>
    </Button>
  );
}
