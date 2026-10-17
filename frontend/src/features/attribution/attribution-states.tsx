import { KeyRound, PieChart } from "lucide-react";

import { CodeBlock } from "@/components/code-block";
import { EmptyState } from "@/components/empty-state";
import type { AttributionGroupBy } from "@/lib/api";

import { dimensionName } from "./attribution-grid";

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

/** Figma "Attribution / Empty": names the key and shows how to set it. */
export function AttributionEmpty({
  groupBy,
  metadataKey,
}: {
  groupBy: AttributionGroupBy;
  metadataKey: string | null;
}) {
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
