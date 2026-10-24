import { KeyRound, PieChart } from "lucide-react";

import { CodeBlock } from "@/components/code-block";
import { EmptyState } from "@/components/empty-state";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AttributionGroupBy } from "@/lib/api";
import { pendingTracesNoun } from "@/lib/freshness";

import { dimensionName } from "./attribution-grid";
import { AttributionTilesSkeleton } from "./attribution-tiles";

/** Rows while the first page loads: the table's from `md`, tiles below, as when loaded. */
export function AttributionLoading() {
  return (
    <Card role="status" aria-label="Loading attribution" className="p-2">
      <div aria-hidden className="hidden flex-col gap-0.5 md:flex">
        <Skeleton className="h-9 w-full" />
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} className="h-10 w-full" />
        ))}
      </div>
      <div className="md:hidden">
        <AttributionTilesSkeleton />
      </div>
    </Card>
  );
}

/** Metadata is picked but no key is: nothing is fetched until one is. */
export function PickKeyPrompt() {
  return (
    <EmptyState
      icon={KeyRound}
      title="Pick a metadata key"
      description="Choose a key above to see what each of its values cost."
    />
  );
}

function sdkSnippet(key: string): string {
  return `import spanlight\n\nspanlight.update_trace(metadata={"${key}": "value"})`;
}

/**
 * Figma "Attribution / Empty": names the key and shows how to set it. When the rollup has no rows
 * yet but the window has traces waiting for it, says so instead of "no traces".
 */
export function AttributionEmpty({
  groupBy,
  metadataKey,
  pendingTraces,
}: {
  groupBy: AttributionGroupBy;
  metadataKey: string | null;
  pendingTraces: number | null;
}) {
  if (pendingTraces !== null && pendingTraces > 0) {
    const verb = pendingTraces === 1 ? "hasn't" : "haven't";
    return (
      <EmptyState
        icon={PieChart}
        title="Not counted yet"
        description={`${pendingTracesNoun(pendingTraces)} in this window ${verb} been added to the daily rollup yet.`}
      />
    );
  }
  if (groupBy !== "metadata" || metadataKey === null) {
    return (
      <EmptyState
        icon={PieChart}
        title="No traces in this window"
        description={`Traces started in this window show up here, grouped by ${dimensionName(groupBy, null)}.`}
      />
    );
  }
  return (
    <EmptyState
      icon={PieChart}
      title={`No traces carried ${metadataKey} in this window`}
      description={
        <div className="flex flex-col gap-3">
          <p>
            Set it on each trace from the SDK, or send it as an OTLP span attribute. Traces that
            carry it show up here.
          </p>
          <CodeBlock
            code={sdkSnippet(metadataKey)}
            label="Python SDK: set metadata"
            language="python"
            className="text-left"
          />
          <p className="text-xs">
            OTLP: set the span attribute{" "}
            <code className="font-mono text-foreground">{`spanlight.metadata.${metadataKey}`}</code>{" "}
            on any span of the trace.
          </p>
        </div>
      }
      className="[&>div:nth-child(2)]:max-w-xl"
    />
  );
}
